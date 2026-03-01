import socket, threading, pyaudio, struct, math, time
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

# Per-stream AGC targets
TARGET_PEAK = 12000
MAX_GAIN = 3.0
MIN_GAIN = 0.5


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
        self.streams = {}          # sender_id -> dict(seq -> (timestamp, pcm, arrival_time))
        self.expected_seq = {}     # sender_id -> next seq
        self.playout_ts = {}       # sender_id -> expected timestamp (samples)
        self.jitter_target = {}    # sender_id -> target frames
        self.jitter_est = {}       # sender_id -> jitter estimate (seconds)
        self.last_arrival = {}     # sender_id -> last arrival time
        self.last_adjust = {}      # sender_id -> last adjust time
        self.stream_levels = {}    # sender_id -> float (EMA of peak)
        self.hear_targets = set()
        self.running = False
        self.listen_running = True
        self.multicast_running = False
        self.stream_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.multicast_sock = None
        self.multicast_group = None
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
        self.last_packet_seq = {}

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

    def set_hear_targets(self, targets):
        self.hear_targets = set(targets)

        # Flush muted streams immediately
        with self.stream_lock:
            for sid in list(self.streams.keys()):
                if sid not in self.hear_targets:
                    del self.streams[sid]
                    self.expected_seq.pop(sid, None)
                    self.playout_ts.pop(sid, None)
                    self.jitter_target.pop(sid, None)
                    self.jitter_est.pop(sid, None)
                    self.last_arrival.pop(sid, None)
                    self.last_adjust.pop(sid, None)
                    self.stream_levels.pop(sid, None)

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

        samples = [0] * (frame_bytes // 2)
        active = 0

        frames = []
        with self.stream_lock:
            for sid in list(self.streams.keys()):
                if sid not in self.hear_targets:
                    continue

                buf = self.streams.get(sid)
                if not buf:
                    continue

                exp = self.expected_seq.get(sid)
                if exp is None:
                    continue

                # Keep buffer bounded to avoid unbounded delay
                while len(buf) > MAX_FRAMES:
                    buf.pop(min(buf.keys()))

                # If source is inactive for a while, reset sequence tracking to avoid PLC spam.
                last_arrival = self.last_arrival.get(sid)
                if last_arrival is not None and (time.time() - last_arrival) > 0.8:
                    buf.clear()
                    self.expected_seq.pop(sid, None)
                    self.playout_ts.pop(sid, None)
                    self.stream_levels[sid] = 0.0
                    continue

                target = self.jitter_target.get(sid, TARGET_FRAMES)
                if len(buf) < max(MIN_FRAMES, target):
                    continue

                if exp in buf:
                    ts, chunk, _arr = buf.pop(exp)
                    # Drop late packets
                    exp_ts = self.playout_ts.get(sid)
                    if exp_ts is not None and ts < exp_ts:
                        self.expected_seq[sid] = (exp + 1) & 0xFFFF
                        continue
                    frames.append((sid, chunk, ts))
                else:
                    # Fast resync if we already have a future packet.
                    if buf:
                        next_seq = min(buf.keys(), key=lambda k: seq_forward_distance(exp, k))
                        gap = seq_forward_distance(exp, next_seq)

                        # Small gap: skip ahead immediately.
                        if 0 < gap <= 8 and next_seq in buf:
                            ts, chunk, _arr = buf.pop(next_seq)
                            frames.append((sid, chunk, ts))
                            self.expected_seq[sid] = (next_seq + 1) & 0xFFFF
                            continue

                        # Larger discontinuity while buffer has data: jump to head to avoid long PLC streaks.
                        if gap > 8 and len(buf) >= max(2, target):
                            ts, chunk, _arr = buf.pop(next_seq)
                            frames.append((sid, chunk, ts))
                            self.expected_seq[sid] = (next_seq + 1) & 0xFFFF
                            continue
                    self.jitter_stats["missing"] += 1
                    if self.jitter_stats["missing"] % 100 == 1:
                        print(f"[JITTER] Missing seq {exp} from {sid}")
                    frames.append((sid, None, None))
                self.expected_seq[sid] = (exp + 1) & 0xFFFF

        for sid, chunk, ts in frames:

            if chunk is None:
                pcm = self.codec.decode(None)
                if not pcm:
                    continue
                chunk = pcm[:frame_bytes]
                exp_ts = self.playout_ts.get(sid)
                if exp_ts is not None:
                    self.playout_ts[sid] = exp_ts + FRAME
            else:
                if ts is not None:
                    self.playout_ts[sid] = ts + FRAME

            data = struct.unpack("<" + "h" * (frame_bytes // 2), chunk)
            peak = max(abs(s) for s in data) or 1

            # Per-stream AGC (EMA on peak)
            prev = self.stream_levels.get(sid, peak)
            level = 0.9 * prev + 0.1 * peak
            self.stream_levels[sid] = level

            gain = TARGET_PEAK / level if level > 0 else 1.0
            gain = max(MIN_GAIN, min(MAX_GAIN, gain))

            data = [int(s * gain) for s in data]
            samples = [a + b for a, b in zip(samples, data)]
            active += 1

        if active == 0:
            return b"\x00" * frame_bytes

        # Soft limiter to prevent clipping without shrinking everything
        def soft_clip(x):
            return int(32767 * math.tanh(x / 32767.0))

        volume_factor = max(0.0, min(2.0, self.master_volume * self.output_volume))
        output_samples = [soft_clip(int(s * volume_factor)) for s in samples]
        output_bytes = struct.pack("<" + "h" * len(output_samples), *output_samples)

        # Limit logging to avoid spam
        if not hasattr(self, "_mix_count"):
            self._mix_count = 0
        self._mix_count += 1
        if self._mix_count % 1000 == 0:
            print(f"[AUDIO] Mixing {active} sources, {self._mix_count} total callbacks")

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
        if b":" not in data:
            print(f"[AUDIO] Malformed packet from {addr}: {data[:50]}")
            return

        header, opus = data.split(b":", 1)
        header = header.decode(errors="ignore")
        if "|" not in header:
            return
        sender_id, seq_s, ts_s = header.split("|", 2)
        try:
            seq = int(seq_s) & 0xFFFF
            ts = int(ts_s)
        except ValueError:
            return
        sender_id = sender_id.strip()

        if sender_id == self.client_id:
            return

        if not hasattr(self, "_packet_count"):
            self._packet_count = {}

        if sender_id not in self._packet_count:
            self._packet_count[sender_id] = 0
            print(f"[AUDIO] First packet from sender: {sender_id}")

        self._packet_count[sender_id] += 1
        self.jitter_stats["received"] += 1

        if self._packet_count[sender_id] % 20 == 1:
            print(f"[AUDIO] Received #{self._packet_count[sender_id]} from {sender_id} (size: {len(opus)} bytes)")

        # Always decode & buffer
        try:
            pcm = self.codec.decode(opus)
            if pcm:
                frame_bytes = CHUNK * 2
                arrival_time = time.time()
                with self.stream_lock:
                    buf = self.streams.setdefault(sender_id, {})
                    exp_ts = self.playout_ts.get(sender_id)
                    if exp_ts is not None and ts < exp_ts:
                        return

                    # Detect discontinuity/restart and resync receive tracking.
                    prev_seq = self.last_packet_seq.get(sender_id)
                    if prev_seq is not None:
                        forward = (seq - prev_seq) & 0xFFFF
                        if forward > 2000:
                            self.expected_seq[sender_id] = seq
                            self.playout_ts[sender_id] = ts
                            buf.clear()
                        elif 40 < forward < 2000:
                            # Large forward jump likely indicates a burst drop/reset; resync quickly.
                            self.expected_seq[sender_id] = seq
                            self.playout_ts[sender_id] = ts
                            buf.clear()
                    self.last_packet_seq[sender_id] = seq

                    buf[seq] = (ts, pcm[:frame_bytes], arrival_time)
                    if sender_id not in self.expected_seq:
                        self.expected_seq[sender_id] = seq
                    if sender_id not in self.playout_ts:
                        self.playout_ts[sender_id] = ts
                    if sender_id not in self.jitter_target:
                        self.jitter_target[sender_id] = TARGET_FRAMES

                    # Jitter estimate (arrival delta vs expected frame time)
                    if sender_id in self.last_arrival:
                        delta = arrival_time - self.last_arrival[sender_id]
                        expected = FRAME / RATE
                        jitter = min(0.200, abs(delta - expected))
                        prev = self.jitter_est.get(sender_id, jitter)
                        self.jitter_est[sender_id] = 0.9 * prev + 0.1 * jitter
                    self.last_arrival[sender_id] = arrival_time

                    # Adapt jitter target ~1x per second
                    last_adj = self.last_adjust.get(sender_id, 0)
                    if arrival_time - last_adj > 1.0:
                        j = self.jitter_est.get(sender_id, 0)
                        tgt = self.jitter_target.get(sender_id, TARGET_FRAMES)
                        if j > 0.020:
                            tgt = min(MAX_FRAMES, tgt + 1)
                        elif j < 0.005:
                            tgt = max(MIN_FRAMES, tgt - 1)
                        self.jitter_target[sender_id] = tgt
                        self.last_adjust[sender_id] = arrival_time
                        if int(arrival_time) % 5 == 0:
                            print(f"[JITTER] {sender_id}: target={tgt} jitter={j*1000:.1f}ms")

                    # Prevent unbounded growth (drop oldest)
                    while len(buf) > MAX_FRAMES:
                        buf.pop(min(buf.keys()))
            else:
                print(f"[AUDIO] Failed to decode Opus from {sender_id}")
        except Exception as e:
            print(f"[AUDIO] Decode error from {sender_id}: {e}")

    def listen_multicast(self):
        while self.listen_running and self.multicast_running:
            try:
                data, addr = self.multicast_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception as e:
                if self.listen_running and self.multicast_running:
                    print(f"[AUDIO] multicast recv error: {e}")
                break
            self._handle_incoming_packet(data, addr)

    def join_multicast(self, multicast_addr):
        if not multicast_addr:
            return
        if self.multicast_group == multicast_addr and self.multicast_sock is not None:
            return

        self.leave_multicast()
        try:
            msock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            msock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            _set_socket_dscp(msock, IP_TOS_EF)
            try:
                msock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            msock.bind(("", AUDIO_PORT))
            mreq = struct.pack("4s4s", socket.inet_aton(multicast_addr), socket.inet_aton("0.0.0.0"))
            msock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            msock.settimeout(1.0)

            self.multicast_sock = msock
            self.multicast_group = multicast_addr
            self.multicast_running = True
            self.multicast_thread = threading.Thread(target=self.listen_multicast, daemon=True)
            self.multicast_thread.start()
            print(f"[AUDIO] Joined multicast group {multicast_addr}:{AUDIO_PORT}")
        except Exception as e:
            print(f"[AUDIO] Failed to join multicast {multicast_addr}:{AUDIO_PORT}: {e}")
            try:
                msock.close()
            except Exception:
                pass
            self.multicast_sock = None
            self.multicast_group = None
            self.multicast_running = False

    def leave_multicast(self):
        self.multicast_running = False
        if self.multicast_sock is not None and self.multicast_group:
            try:
                mreq = struct.pack("4s4s", socket.inet_aton(self.multicast_group), socket.inet_aton("0.0.0.0"))
                self.multicast_sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            except Exception:
                pass
        try:
            if self.multicast_sock is not None:
                self.multicast_sock.close()
        except Exception:
            pass
        self.multicast_sock = None
        self.multicast_group = None

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
        self.leave_multicast()
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
