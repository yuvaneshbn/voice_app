import socket
import struct
import threading
import time
from collections import deque

from typing import Optional

import pyaudio

from echo_cancel import EchoCanceller, echo_cancel_available
from opus_codec import OpusCodec

RATE = 16000
FRAME = 320  # 20 ms @ 16 kHz
CHUNK = FRAME
AUDIO_PORT = 50002
DSCP_EF = 46
IP_TOS_EF = DSCP_EF << 2
RX_QUEUE_MAX_FRAMES = 8
DEVICE_HOST_PRIORITY = {
    "windows wasapi": 0,
    "windows wdm-ks": 1,
    "windows directsound": 2,
    "mme": 3,
}
GENERIC_DEVICE_NAMES = {
    "microsoft sound mapper - input",
    "microsoft sound mapper - output",
    "primary sound capture driver",
    "primary sound driver",
}


def _set_socket_dscp(sock, ip_tos):
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, ip_tos)
    except OSError:
        pass


class AudioEngine:
    def __init__(self):
        self.client_id = None
        self.audio = pyaudio.PyAudio()
        self.codec = OpusCodec(rate=RATE, channels=1, frame_size=FRAME)

        # Receive socket
        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        _set_socket_dscp(self.recv_sock, IP_TOS_EF)
        self.recv_sock.bind(("", 0))
        self.port = self.recv_sock.getsockname()[1]

        # Playback state (server sends mixed room stream)
        self.rx_frames = deque(maxlen=RX_QUEUE_MAX_FRAMES)
        self.stream_levels = {}
        self.jitter_stats = {"missing": 0, "received": 0}

        self.running = False
        self.listen_running = True
        self.stream_lock = threading.Lock()
        self.state_lock = threading.Lock()

        self.input = None
        self.output = None
        self.send_sock = None
        self.send_thread = None
        self.listen_thread = None
        self.server_ip = None
        self.route_role = "leaf"
        self.route_uplink: Optional[tuple[str, int]] = None
        self.route_downlinks = {}
        self.route_lock = threading.Lock()
        self.route_ttl = 8
        self.forward_cache = deque(maxlen=2048)

        # Runtime controls
        self.master_volume = 1.0
        self.output_volume = 1.0
        self.tx_gain_db = 0.0
        self.mic_sensitivity = 50
        self.noise_suppression = 0
        self.noise_suppression_enabled = False
        self.auto_gain = False
        self.tx_muted = False
        self.capture_level = 0
        self.capture_active = False
        self.input_device_index = None
        self.output_device_index = None

        # Legacy echo wrapper: keep available, but off by default to avoid pipeline conflicts.
        self.echo = None
        self.echo_enabled = False
        self.echo_lock = threading.Lock()
        if echo_cancel_available():
            try:
                self.echo = EchoCanceller(sample_rate=RATE, channels=1, frame_size=FRAME, delay_ms=60)
                print("[AUDIO] Native echo cancellation available (disabled by default)")
            except Exception as e:
                print(f"[AUDIO] Native echo cancellation unavailable: {e}")
        else:
            print("[AUDIO] Native echo cancellation API not found in native_mixer.dll")

        self.last_playout = b"\x00" * (FRAME * 2)
        self.seq = 0
        self.timestamp = 0

        self._open_output_stream()
        self.listen_thread = threading.Thread(target=self.listen, daemon=True, name="audio-listen")
        self.listen_thread.start()

    def _open_output_stream(self):
        if self.output is not None:
            try:
                self.output.stop_stream()
                self.output.close()
            except Exception:
                pass

        kwargs = dict(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            output=True,
            frames_per_buffer=CHUNK,
            stream_callback=self._callback,
        )
        if self.output_device_index is not None:
            kwargs["output_device_index"] = self.output_device_index

        self.output = self.audio.open(**kwargs)
        self.output.start_stream()

    def _device_host_name(self, info):
        try:
            host_index = int(info.get("hostApi", -1))
        except (TypeError, ValueError):
            host_index = -1
        if host_index < 0:
            return ""
        try:
            host_info = self.audio.get_host_api_info_by_index(host_index)
        except Exception:
            return ""
        return str(host_info.get("name", "")).strip()

    def _list_devices(self, channel_key, fallback_name):
        entries = []
        try:
            for i in range(self.audio.get_device_count()):
                info = self.audio.get_device_info_by_index(i)
                if int(info.get(channel_key, 0)) <= 0:
                    continue

                raw_name = str(info.get("name", f"{fallback_name} {i}")).strip()
                if not raw_name:
                    raw_name = f"{fallback_name} {i}"
                host_name = self._device_host_name(info)
                display_name = f"{raw_name} [{host_name}]" if host_name else raw_name

                rank = DEVICE_HOST_PRIORITY.get(host_name.casefold(), 99)
                key = raw_name.casefold()
                entries.append(
                    {
                        "rank": rank,
                        "index": i,
                        "raw_name": raw_name,
                        "display_name": display_name,
                        "is_generic": key in GENERIC_DEVICE_NAMES,
                    }
                )
        except Exception:
            return []

        if not entries:
            return []

        candidates = [entry for entry in entries if not entry["is_generic"]] or entries
        best_rank = min(entry["rank"] for entry in candidates)
        selected = [entry for entry in candidates if entry["rank"] == best_rank]

        deduped = {}
        for entry in selected:
            key = entry["raw_name"].casefold()
            current = deduped.get(key)
            if current is None or entry["index"] < current["index"]:
                deduped[key] = entry

        return [
            (entry["index"], entry["display_name"])
            for entry in sorted(
                deduped.values(),
                key=lambda item: (item["raw_name"].casefold(), item["index"]),
            )
        ]

    def list_input_devices(self):
        return self._list_devices("maxInputChannels", "Input")

    def list_output_devices(self):
        return self._list_devices("maxOutputChannels", "Output")

    def set_input_device(self, device_index):
        self.input_device_index = device_index
        if self.running and self.server_ip:
            self.stop()
            self.start(self.server_ip)
        return True

    def set_output_device(self, device_index):
        self.output_device_index = device_index
        try:
            self._open_output_stream()
            return True
        except Exception as e:
            print(f"[AUDIO] Failed to set output device {device_index}: {e}")
            return False

    def set_master_volume(self, value):
        self.master_volume = max(0.0, min(2.0, float(value) / 100.0))

    def set_output_volume(self, value):
        self.output_volume = max(0.0, min(2.0, float(value) / 100.0))

    def set_gain_db(self, value):
        self.tx_gain_db = max(-20.0, min(20.0, float(value)))

    def set_mic_sensitivity(self, value):
        self.mic_sensitivity = max(0, min(100, int(value)))

    def set_noise_suppression(self, value):
        self.noise_suppression = max(0, min(100, int(value)))

    def set_noise_suppression_enabled(self, enabled):
        self.noise_suppression_enabled = bool(enabled)

    def set_auto_gain(self, enabled):
        self.auto_gain = bool(enabled)

    def set_echo_enabled(self, enabled):
        with self.echo_lock:
            self.echo_enabled = bool(enabled) and self.echo is not None

    def set_tx_muted(self, enabled):
        self.tx_muted = bool(enabled)

    def test_microphone_level(self, duration_sec=1.0):
        duration_sec = max(0.2, float(duration_sec))
        max_peak = 0
        stream = None
        try:
            kwargs = dict(
                format=pyaudio.paInt16,
                channels=1,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )
            if self.input_device_index is not None:
                kwargs["input_device_index"] = self.input_device_index
            stream = self.audio.open(**kwargs)
            deadline = time.time() + duration_sec
            while time.time() < deadline:
                pcm = stream.read(CHUNK, exception_on_overflow=False)
                samples = struct.unpack("<" + "h" * CHUNK, pcm)
                peak = max(abs(s) for s in samples) if samples else 0
                max_peak = max(max_peak, peak)
        except Exception as e:
            print(f"[AUDIO] Mic test failed: {e}")
        finally:
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
        return min(100, int((max_peak * 100) / 32767)) if max_peak else 0

    def _callback(self, in_data, frame_count, *_):
        frame_bytes = frame_count * 2
        mixed_pcm = self.mix(frame_bytes)
        self.last_playout = mixed_pcm
        if self.echo_enabled:
            try:
                with self.echo_lock:
                    if self.echo_enabled and self.echo is not None:
                        self.echo.process_reverse(mixed_pcm)
            except Exception as e:
                print(f"[AUDIO] Echo reverse error, disabling echo canceller: {e}")
                self.echo_enabled = False
        return (mixed_pcm, pyaudio.paContinue)

    def mix(self, frame_bytes):
        with self.stream_lock:
            chunk = self.rx_frames.popleft() if self.rx_frames else None

        if chunk is None:
            self.jitter_stats["missing"] += 1
            plc = self.codec.decode(None)
            if plc:
                chunk = plc[:frame_bytes]
            else:
                chunk = b"\x00" * frame_bytes

        if len(chunk) != frame_bytes:
            chunk = (chunk + (b"\x00" * frame_bytes))[:frame_bytes]

        samples = struct.unpack("<" + "h" * (frame_bytes // 2), chunk)
        peak = max(abs(sample) for sample in samples) if samples else 0

        volume_factor = max(0.0, min(2.0, self.master_volume * self.output_volume))
        if volume_factor != 1.0:
            samples = [
                max(-32768, min(32767, int(sample * volume_factor)))
                for sample in samples
            ]
        output_bytes = struct.pack("<" + "h" * len(samples), *samples)

        with self.stream_lock:
            prev = self.stream_levels.get("__mixed__", peak)
            self.stream_levels["__mixed__"] = (0.9 * prev) + (0.1 * peak)

        return output_bytes

    def listen(self):
        print(f"[AUDIO] Listening for audio on port {self.port}")
        while self.listen_running:
            try:
                data, addr = self.recv_sock.recvfrom(4096)
            except Exception as e:
                if self.listen_running:
                    print(f"[AUDIO] recv_sock error: {e}")
                continue
            self._handle_incoming_packet(data, addr)

    def update_route(self, role, uplink, downlinks):
        with self.route_lock:
            self.route_role = role if role in ("router", "leaf") else "leaf"
            self.route_uplink = uplink
            self.route_downlinks = dict(downlinks or {})

    def _parse_routed_packet(self, data):
        if not data.startswith(b"ROUTE|") or b":" not in data:
            return None
        header, opus = data.split(b":", 1)
        parts = header.decode(errors="ignore").split("|")
        if len(parts) != 5:
            return None
        _tag, origin_id, seq_raw, ts_raw, ttl_raw = parts
        try:
            seq = int(seq_raw)
            timestamp = int(ts_raw)
            ttl = int(ttl_raw)
        except ValueError:
            return None
        return origin_id, seq, timestamp, ttl, opus

    @staticmethod
    def _build_routed_packet(origin_id, seq, timestamp, ttl, opus):
        header = f"ROUTE|{origin_id}|{seq}|{timestamp}|{ttl}".encode()
        return header + b":" + opus

    def _forward_routed_packet(self, packet, source_addr, ttl):
        if ttl <= 0:
            return
        out_sock = self.send_sock or self.recv_sock
        src = tuple(source_addr) if source_addr else None
        with self.route_lock:
            if self.route_role != "router":
                return
            uplink = self.route_uplink
            downlinks = dict(self.route_downlinks)

        targets = []
        if uplink and src == uplink:
            targets = [addr for addr in downlinks.values() if addr != src]
        elif src in downlinks.values():
            if uplink is not None:
                targets.append(uplink)
            targets.extend(addr for addr in downlinks.values() if addr != src)
        else:
            if uplink is not None:
                targets.append(uplink)
            targets.extend(downlinks.values())

        unique = []
        seen = set()
        for target in targets:
            if target in seen:
                continue
            seen.add(target)
            unique.append(target)

        for target in unique:
            try:
                out_sock.sendto(packet, target)
            except Exception as e:
                print(f"[AUDIO] Route forward error to {target}: {e}")

    def _handle_incoming_packet(self, data, addr):
        routed = self._parse_routed_packet(data)
        if routed is not None:
            origin_id, seq, timestamp, ttl, opus = routed
            cache_key = (origin_id, seq)
            if cache_key in self.forward_cache:
                return
            self.forward_cache.append(cache_key)

            if self.route_role == "router" and ttl > 0:
                fwd_packet = self._build_routed_packet(origin_id, seq, timestamp, ttl - 1, opus)
                self._forward_routed_packet(fwd_packet, addr, ttl - 1)

            if origin_id == self.client_id:
                return
        else:
            opus = b""
            if data.startswith(b"MIXED|"):
                try:
                    _tag, _seq_raw, opus = data.split(b"|", 2)
                except Exception:
                    return
            elif b":" in data:
                header, opus = data.split(b":", 1)
                header_s = header.decode(errors="ignore")
                sender_id = header_s.split("|", 1)[0].strip()
                if sender_id and sender_id == self.client_id:
                    return
            else:
                opus = data

        if not opus:
            return

        try:
            pcm = self.codec.decode(opus)
        except Exception as e:
            print(f"[AUDIO] Decode error from {addr}: {e}")
            return

        if not pcm:
            return

        frame_bytes = CHUNK * 2
        pcm = (pcm + (b"\x00" * frame_bytes))[:frame_bytes]

        with self.stream_lock:
            self.rx_frames.append(pcm)
        self.jitter_stats["received"] += 1

    def start(self, server_ip):
        if not self.client_id:
            return

        with self.state_lock:
            if self.running:
                return
            if self.send_thread and self.send_thread.is_alive():
                self.send_thread.join(timeout=1.0)
                if self.send_thread.is_alive():
                    print("[AUDIO] Capture restart skipped: previous send thread still active")
                    return

            self.running = True
            self.server_ip = server_ip
            print(f"[AUDIO] Audio capture ACTIVE for {self.client_id} -> {server_ip}:{AUDIO_PORT}")

        try:
            input_kwargs = dict(
                format=pyaudio.paInt16,
                channels=1,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )
            if self.input_device_index is not None:
                input_kwargs["input_device_index"] = self.input_device_index
            self.input = self.audio.open(**input_kwargs)

            self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
            _set_socket_dscp(self.send_sock, IP_TOS_EF)
        except Exception as e:
            with self.state_lock:
                self.running = False
            try:
                if self.input is not None:
                    self.input.close()
            except Exception:
                pass
            self.input = None
            try:
                if self.send_sock is not None:
                    self.send_sock.close()
            except Exception:
                pass
            self.send_sock = None
            print(f"[AUDIO] Failed to start capture: {e}")
            return

        def send_loop():
            packet_count = 0
            while self.running:
                try:
                    pcm = self.input.read(CHUNK, exception_on_overflow=False)
                    samples = list(struct.unpack("<" + "h" * CHUNK, pcm))
                    input_peak = max(abs(s) for s in samples) if samples else 0

                    self.capture_level = min(100, int((input_peak * 100) / 32767))
                    activity_threshold = max(120, int(2200 - (self.mic_sensitivity * 16)))
                    self.capture_active = input_peak >= activity_threshold and not self.tx_muted

                    if self.tx_muted:
                        samples = [0] * len(samples)
                    else:
                        if self.noise_suppression_enabled:
                            gate = int((self.noise_suppression / 100.0) * 2500)
                            if gate > 0:
                                samples = [s if abs(s) >= gate else 0 for s in samples]

                        gain = 10 ** (self.tx_gain_db / 20.0)
                        gain *= 0.5 + (self.mic_sensitivity / 100.0)
                        if self.auto_gain:
                            target = 9000 + (self.mic_sensitivity * 80)
                            gain *= max(0.5, min(3.0, target / max(input_peak, 1)))

                        if gain != 1.0:
                            samples = [max(-32768, min(32767, int(s * gain))) for s in samples]

                    pcm = struct.pack("<" + "h" * len(samples), *samples)
                    if self.echo_enabled:
                        try:
                            with self.echo_lock:
                                if self.echo_enabled and self.echo is not None:
                                    pcm = self.echo.process_capture(pcm)
                        except Exception as e:
                            print(f"[AUDIO] Echo capture error, disabling echo canceller: {e}")
                            self.echo_enabled = False

                    opus = self.codec.encode(pcm)
                    if opus:
                        if not self.running or self.send_sock is None:
                            break
                        with self.route_lock:
                            uplink = self.route_uplink
                        target = uplink if uplink is not None else (server_ip, AUDIO_PORT)
                        packet = self._build_routed_packet(self.client_id, self.seq, self.timestamp, self.route_ttl, opus)
                        self.seq = (self.seq + 1) & 0xFFFF
                        self.timestamp += FRAME
                        self.send_sock.sendto(packet, target)
                        packet_count += 1
                        if packet_count % 100 == 0:
                            print(f"[AUDIO] Sent {packet_count} packets from {self.client_id}")
                except Exception as e:
                    if not self.running:
                        break
                    if isinstance(e, OSError) and getattr(e, "winerror", None) == 10038:
                        break
                    print(f"[AUDIO] Send error: {e}")

        self.send_thread = threading.Thread(target=send_loop, daemon=True, name="audio-send")
        self.send_thread.start()

    def stop(self):
        with self.state_lock:
            self.running = False
            send_thread = self.send_thread
            self.send_thread = None

        self.capture_active = False
        self.capture_level = 0

        if send_thread and send_thread.is_alive():
            send_thread.join(timeout=1.5)

        try:
            if self.input is not None:
                self.input.stop_stream()
                self.input.close()
                self.input = None
        except Exception:
            pass

        try:
            if self.send_sock is not None:
                self.send_sock.close()
                self.send_sock = None
        except Exception:
            pass

    def shutdown(self):
        self.stop()
        self.listen_running = False

        with self.echo_lock:
            if self.echo is not None:
                try:
                    self.echo.close()
                except Exception:
                    pass
                self.echo = None
            self.echo_enabled = False

        try:
            self.recv_sock.close()
        except Exception:
            pass

        if self.listen_thread and self.listen_thread.is_alive():
            self.listen_thread.join(timeout=1.0)

        try:
            if self.output is not None:
                self.output.stop_stream()
                self.output.close()
                self.output = None
        except Exception:
            pass

        try:
            self.audio.terminate()
        except Exception:
            pass
