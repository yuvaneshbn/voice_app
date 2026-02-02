import socket, threading, pyaudio, struct
from opus_codec import OpusCodec

RATE  = 16000
FRAME = 320        # 20 ms @ 16 kHz (matches OpusCodec default)
CHUNK = FRAME


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
        self.streams = {}          # sender_id -> bytearray
        self.hear_targets = set()
        self.running = False

        # ================= OUTPUT STREAM =================
        self.output = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            output=True,
            frames_per_buffer=CHUNK,
            stream_callback=self._callback
        )
        self.output.start_stream()

        threading.Thread(target=self.listen, daemon=True).start()

    # --------------------------------------------------

    def set_hear_targets(self, targets):
        self.hear_targets = set(targets)

        # 🔧 FIX: flush muted streams immediately
        for sid in list(self.streams.keys()):
            if sid not in self.hear_targets:
                del self.streams[sid]

    # --------------------------------------------------

    def _callback(self, in_data, frame_count, *_):
        frame_bytes = frame_count * 2
        return (self.mix(frame_bytes), pyaudio.paContinue)

    # --------------------------------------------------

    def mix(self, frame_bytes):
        samples = [0] * (frame_bytes // 2)
        active = 0

        # 🔧 FIX: mix ONLY currently-heard targets
        for sid in list(self.streams.keys()):
            if sid not in self.hear_targets:
                continue

            buf = self.streams[sid]
            if len(buf) >= frame_bytes:
                chunk = buf[:frame_bytes]
                del buf[:frame_bytes]

                data = struct.unpack("<" + "h" * (frame_bytes // 2), chunk)
                samples = [a + b for a, b in zip(samples, data)]
                active += 1

        if active == 0:
            return b"\x00" * frame_bytes

        max_val = max(abs(s) for s in samples) or 1
        scale = min(1.0, 32767 / max_val)
        
        output_bytes = struct.pack(
            "<" + "h" * len(samples),
            *[int(s * scale) for s in samples]
        )
        
        # Limit logging to avoid spam
        if not hasattr(self, '_mix_count'):
            self._mix_count = 0
        self._mix_count += 1
        if self._mix_count % 1000 == 0:
            print(f"[AUDIO] 🔊 Mixing {active} sources, {self._mix_count} total callbacks")
        
        return output_bytes

    # --------------------------------------------------

    def listen(self):
        print(f"[AUDIO] 🎧 Listening for audio on port {self.port}")
        packet_count = {}
        while True:
            try:
                data, addr = self.recv_sock.recvfrom(4096)
            except Exception as e:
                print(f"[AUDIO] ❌ recv_sock error: {e}")
                continue

            if b":" not in data:
                print(f"[AUDIO] ❌ Malformed packet from {addr}: {data[:50]}")
                continue

            sender, opus = data.split(b":", 1)
            sender_id = sender.decode(errors="ignore").strip()
            
            if sender_id not in packet_count:
                packet_count[sender_id] = 0
                print(f"[AUDIO] 🎧 First packet from sender: {sender_id}")
            
            packet_count[sender_id] += 1
            
            # Log more frequently for diagnostics
            if packet_count[sender_id] % 20 == 1:
                print(f"[AUDIO] 🎧 Received #{packet_count[sender_id]} from {sender_id} (size: {len(opus)} bytes)")

            # 🔧 FIX: ALWAYS decode & buffer
            try:
                pcm = self.codec.decode(opus)
                if pcm:
                    self.streams.setdefault(sender_id, bytearray()).extend(pcm)
                else:
                    print(f"[AUDIO] ❌ Failed to decode Opus from {sender_id}")
            except Exception as e:
                print(f"[AUDIO] ❌ Decode error from {sender_id}: {e}")

    # --------------------------------------------------

    def start(self, server_ip):
        if self.running or not self.client_id:
            return

        self.running = True
        print(f"[AUDIO] ✅ Audio capture ACTIVE for {self.client_id} → {server_ip}:50002")

        self.input = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)

        def send():
            packet_count = 0
            while self.running:
                try:
                    pcm = self.input.read(CHUNK, exception_on_overflow=False)
                    opus = self.codec.encode(pcm)
                    if opus:
                        packet = self.client_id.encode() + b":" + opus
                        sock.sendto(packet, (server_ip, 50002))
                        packet_count += 1
                        if packet_count % 100 == 0:
                            print(f"[AUDIO] 🎤 Sent {packet_count} packets from {self.client_id}")
                except Exception as e:
                    print(f"[AUDIO] ❌ Send error: {e}")

        threading.Thread(target=send, daemon=True).start()

    # --------------------------------------------------

    def stop(self):
        self.running = False
