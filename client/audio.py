import socket, threading, pyaudio, struct, time
from opus_codec import OpusCodec
from echo_cancel import EchoCanceller, echo_cancel_available

RATE = 16000
FRAME = 320  # 20 ms @ 16 kHz (matches OpusCodec default)
CHUNK = FRAME
FRAME_MS = int(1000 * FRAME / RATE)
AUDIO_PORT = 50002
DSCP_EF = 46
IP_TOS_EF = DSCP_EF << 2

# Simple jitter buffer targets (ms)
JITTER_MIN_MS = 20
JITTER_TARGET_MS = 60
JITTER_MAX_MS = 120

MIN_FRAMES = max(1, JITTER_MIN_MS // FRAME_MS)
TARGET_FRAMES = max(1, JITTER_TARGET_MS // FRAME_MS)
MAX_FRAMES = max(2, JITTER_MAX_MS // FRAME_MS)

def _set_socket_dscp(sock, ip_tos):
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, ip_tos)
    except OSError:
        pass


class AudioEngine:
    def __init__(self):
        self.client_id = None
        self.audio = pyaudio.PyAudio()

        # Opus codec (frame size MUST match)
        self.codec = OpusCodec(rate=RATE, channels=1, frame_size=FRAME)

        # ================= RECEIVE SOCKET =================
        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        _set_socket_dscp(self.recv_sock, IP_TOS_EF)

        # Bind to ephemeral port
        self.recv_sock.bind(("", 0))
        self.port = self.recv_sock.getsockname()[1]

        # ================= AUDIO STATE =================
        # Server-side mixing mode: one mixed stream per room.
        self.rx_buffer = {}        # seq -> (pcm_bytes, arrival_time)
        self.rx_expected_seq = None
        self.rx_jitter_target = TARGET_FRAMES
        self.rx_jitter_est = 0.0
        self.rx_last_arrival = None
        self.rx_last_adjust = 0.0
        self.rx_synth_seq = 0
        self.stream_levels = {}
        self.running = False
        self.listen_running = True
        self.stream_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.input = None
        self.send_sock = None
        self.send_thread = None
        self.server_ip = None

        # Runtime audio controls (wired from UI)
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

        self.echo = None
        self.echo_enabled = False
        self.echo_lock = threading.Lock()
        if echo_cancel_available():
            try:
                self.echo = EchoCanceller(sample_rate=RATE, channels=1, frame_size=FRAME, delay_ms=60)
                self.echo_enabled = True
                print("[AUDIO] Native echo cancellation enabled")
            except Exception as e:
                print(f"[AUDIO] Native echo cancellation unavailable: {e}")
        else:
            print("[AUDIO] Native echo cancellation API not found in native_mixer.dll")

        self.last_playout = b"\x00" * (FRAME * 2)
        self.seq = 0
        self.timestamp = 0
        self.jitter_stats = {"missing": 0, "received": 0}

        # ================= OUTPUT STREAM =================
        self.output = None
        self._open_output_stream()

        self.listen_thread = threading.Thread(target=self.listen, daemon=True)
        self.listen_thread.start()

    # --------------------------------------------------

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

    # --------------------------------------------------

    def list_input_devices(self):
        devices = []
        try:
            for i in range(self.audio.get_device_count()):
                info = self.audio.get_device_info_by_index(i)
                if int(info.get("maxInputChannels", 0)) > 0:
                    devices.append((i, info.get("name", f"Input {i}")))
        except Exception:
            pass
        return devices

    def list_output_devices(self):
        devices = []
        try:
            for i in range(self.audio.get_device_count()):
                info = self.audio.get_device_info_by_index(i)
                if int(info.get("maxOutputChannels", 0)) > 0:
                    devices.append((i, info.get("name", f"Output {i}")))
        except Exception:
            pass
        return devices

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

    # --------------------------------------------------

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

    # --------------------------------------------------

    def mix(self, frame_bytes):
        def seq_forward_distance(expected, candidate):
            return (candidate - expected) & 0xFFFF

        chunk = None
        with self.stream_lock:
            if self.rx_expected_seq is not None:
                target = max(MIN_FRAMES, self.rx_jitter_target)
                if len(self.rx_buffer) >= target:
                    exp = self.rx_expected_seq
                    if exp in self.rx_buffer:
                        chunk, _arr = self.rx_buffer.pop(exp)
                        self.rx_expected_seq = (exp + 1) & 0xFFFF
                    elif self.rx_buffer:
                        next_seq = min(
                            self.rx_buffer.keys(),
                            key=lambda key: seq_forward_distance(exp, key),
                        )
                        gap = seq_forward_distance(exp, next_seq)
                        if 0 < gap <= 8 or len(self.rx_buffer) >= target + 2:
                            chunk, _arr = self.rx_buffer.pop(next_seq)
                            self.rx_expected_seq = (next_seq + 1) & 0xFFFF
                        else:
                            self.rx_expected_seq = (exp + 1) & 0xFFFF

        if chunk is None:
            self.jitter_stats["missing"] += 1
            plc = self.codec.decode(None)
            if plc:
                chunk = plc[:frame_bytes]
            else:
                chunk = b"\x00" * frame_bytes

        if len(chunk) != frame_bytes:
            chunk = (chunk + (b"\x00" * frame_bytes))[:frame_bytes]

        data = struct.unpack("<" + "h" * (frame_bytes // 2), chunk)
        peak = max(abs(sample) for sample in data) if data else 0

        volume_factor = max(0.0, min(2.0, self.master_volume * self.output_volume))
        if volume_factor != 1.0:
            data = [
                max(-32768, min(32767, int(sample * volume_factor)))
                for sample in data
            ]
        output_bytes = struct.pack("<" + "h" * len(data), *data)

        with self.stream_lock:
            prev = self.stream_levels.get("__mixed__", peak)
            level = (0.9 * prev) + (0.1 * peak)
            self.stream_levels["__mixed__"] = level

        return output_bytes

    # --------------------------------------------------

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

    def _handle_incoming_packet(self, data, addr):
        seq = None
        opus = b""

        if data.startswith(b"MIXED|"):
            try:
                _tag, seq_raw, opus = data.split(b"|", 2)
                seq = int(seq_raw) & 0xFFFF
            except Exception:
                return
        elif b":" in data:
            # Backward compatibility with older server mode.
            header, opus = data.split(b":", 1)
            header_s = header.decode(errors="ignore")
            if "|" in header_s:
                parts = header_s.split("|")
                sender_id = parts[0].strip()
                if sender_id == self.client_id:
                    return
                if len(parts) > 1:
                    try:
                        seq = int(parts[1]) & 0xFFFF
                    except ValueError:
                        seq = None
            else:
                return
        else:
            # Plain mixed Opus packet (no header).
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
        arrival_time = time.time()
        self.jitter_stats["received"] += 1

        with self.stream_lock:
            if seq is None:
                seq = self.rx_synth_seq
                self.rx_synth_seq = (self.rx_synth_seq + 1) & 0xFFFF

            self.rx_buffer[seq] = (pcm, arrival_time)
            if self.rx_expected_seq is None:
                self.rx_expected_seq = seq

            if self.rx_last_arrival is not None:
                delta = arrival_time - self.rx_last_arrival
                expected = FRAME / RATE
                jitter = min(0.200, abs(delta - expected))
                self.rx_jitter_est = (0.9 * self.rx_jitter_est) + (0.1 * jitter)
            self.rx_last_arrival = arrival_time

            if arrival_time - self.rx_last_adjust > 1.0:
                if self.rx_jitter_est > 0.020:
                    self.rx_jitter_target = min(MAX_FRAMES, self.rx_jitter_target + 1)
                elif self.rx_jitter_est < 0.005:
                    self.rx_jitter_target = max(MIN_FRAMES, self.rx_jitter_target - 1)
                self.rx_last_adjust = arrival_time

            while len(self.rx_buffer) > MAX_FRAMES:
                self.rx_buffer.pop(min(self.rx_buffer.keys()), None)

    # --------------------------------------------------

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
            print(f"[AUDIO] Audio capture ACTIVE for {self.client_id} -> {server_ip}:50002")

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

        def send():
            packet_count = 0
            while self.running:
                try:
                    pcm = self.input.read(CHUNK, exception_on_overflow=False)
                    samples = list(struct.unpack("<" + "h" * CHUNK, pcm))
                    input_peak = max(abs(s) for s in samples) if samples else 0

                    # Capture telemetry for UI meters.
                    self.capture_level = min(100, int((input_peak * 100) / 32767))
                    activity_threshold = max(120, int(2200 - (self.mic_sensitivity * 16)))
                    self.capture_active = input_peak >= activity_threshold and not self.tx_muted

                    if self.tx_muted:
                        samples = [0] * len(samples)
                    else:
                        # Noise suppression slider behaves like a simple gate.
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
                        header = f"{self.client_id}|{self.seq}|{self.timestamp}".encode()
                        packet = header + b":" + opus
                        self.seq = (self.seq + 1) & 0xFFFF
                        self.timestamp += FRAME
                        self.send_sock.sendto(packet, (server_ip, 50002))
                        packet_count += 1
                        if packet_count % 100 == 0:
                            print(f"[AUDIO] Sent {packet_count} packets from {self.client_id}")
                except Exception as e:
                    if not self.running:
                        break
                    if isinstance(e, OSError) and getattr(e, "winerror", None) == 10038:
                        break
                    print(f"[AUDIO] Send error: {e}")

        self.send_thread = threading.Thread(target=send, daemon=True)
        self.send_thread.start()

    # --------------------------------------------------

    def stop(self):
        # Stop capture only (keep receive/output alive)
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
        # Full shutdown (called on app exit)
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

        try:
            if self.output is not None:
                self.output.stop_stream()
                self.output.close()
        except Exception:
            pass

        try:
            self.audio.terminate()
        except Exception:
            pass
