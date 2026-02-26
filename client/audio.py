import socket, threading, pyaudio, struct, math, time
from opus_codec import OpusCodec
from echo_cancel import EchoCanceller, echo_cancel_available

RATE = 16000
FRAME = 320  # 20 ms @ 16 kHz (matches OpusCodec default)
CHUNK = FRAME
FRAME_MS = int(1000 * FRAME / RATE)
AUDIO_PORT = 50002

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
        self.multicast_sock = None
        self.multicast_group = None
        self.input = None
        self.send_sock = None
        self.send_thread = None

        self.echo = None
        self.echo_enabled = False
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
        self.output = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            output=True,
            frames_per_buffer=CHUNK,
            stream_callback=self._callback,
        )
        self.output.start_stream()

        self.listen_thread = threading.Thread(target=self.listen, daemon=True)
        self.listen_thread.start()

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

    def _callback(self, in_data, frame_count, *_):
        frame_bytes = frame_count * 2
        mixed_pcm = self.mix(frame_bytes)
        self.last_playout = mixed_pcm
        if self.echo_enabled and self.echo is not None:
            try:
                self.echo.process_reverse(mixed_pcm)
            except Exception as e:
                print(f"[AUDIO] Echo reverse error, disabling echo canceller: {e}")
                self.echo_enabled = False
        return (mixed_pcm, pyaudio.paContinue)

    # --------------------------------------------------

    def mix(self, frame_bytes):
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

        output_bytes = struct.pack("<" + "h" * len(samples), *[soft_clip(s) for s in samples])

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
                        jitter = abs(delta - expected)
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
        if self.running or not self.client_id:
            return

        self.running = True
        print(f"[AUDIO] Audio capture ACTIVE for {self.client_id} -> {server_ip}:50002")

        self.input = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )

        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)

        def send():
            packet_count = 0
            while self.running:
                try:
                    pcm = self.input.read(CHUNK, exception_on_overflow=False)
                    if self.echo_enabled and self.echo is not None:
                        try:
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
        self.running = False
        if self.send_thread and self.send_thread.is_alive():
            self.send_thread.join(timeout=1.0)

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
        self.running = False
        self.listen_running = False
        self.leave_multicast()
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
            self.output.stop_stream()
            self.output.close()
        except Exception:
            pass

        try:
            self.audio.terminate()
        except Exception:
            pass
