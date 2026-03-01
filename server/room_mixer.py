import queue
import threading
import time
from collections import deque

from opus_codec import OpusCodec, OPUS_APPLICATION_AUDIO

FRAME_SAMPLES = 320
FRAME_BYTES = FRAME_SAMPLES * 2
RATE = 16000
SOURCE_QUEUE_MAX = 6
SOURCE_STALE_SEC = 3.0


def _mix_pcm_frames(frames):
    if not frames:
        return b"\x00" * FRAME_BYTES

    mixed = [0] * FRAME_SAMPLES
    for frame in frames:
        pcm = (frame + (b"\x00" * FRAME_BYTES))[:FRAME_BYTES]
        samples = memoryview(pcm).cast("h")
        for idx in range(FRAME_SAMPLES):
            mixed[idx] += int(samples[idx])

    out = bytearray(FRAME_BYTES)
    out_samples = memoryview(out).cast("h")
    for idx, value in enumerate(mixed):
        if value > 32767:
            out_samples[idx] = 32767
        elif value < -32768:
            out_samples[idx] = -32768
        else:
            out_samples[idx] = value
    return bytes(out)


class PersonalizedMixer:
    def __init__(self, listener_id):
        self.listener_id = listener_id
        self._sources = {}
        self._last_seen = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._encoder = OpusCodec(
            rate=RATE,
            channels=1,
            frame_size=FRAME_SAMPLES,
            bitrate=48000,
            complexity=10,
            enable_fec=True,
            packet_loss_perc=15,
            enable_dtx=False,
            application=OPUS_APPLICATION_AUDIO,
            create_encoder=True,
            create_decoder=False,
        )
        self.mixed_queue = queue.Queue(maxsize=64)
        self.running = False
        self._mix_thread = None

    def add_pcm(self, sender_id, pcm_bytes):
        if not pcm_bytes:
            return
        pcm = (pcm_bytes + (b"\x00" * FRAME_BYTES))[:FRAME_BYTES]
        now = time.time()
        with self._lock:
            source = self._sources.get(sender_id)
            if source is None:
                source = deque(maxlen=SOURCE_QUEUE_MAX)
                self._sources[sender_id] = source
            source.append(pcm)
            self._last_seen[sender_id] = now

    def remove_source(self, sender_id):
        with self._lock:
            self._sources.pop(sender_id, None)
            self._last_seen.pop(sender_id, None)

    def _collect_frames_locked(self):
        now = time.time()
        stale_ids = [
            sender_id
            for sender_id, last_seen in self._last_seen.items()
            if (now - last_seen) > SOURCE_STALE_SEC
        ]
        for sender_id in stale_ids:
            self._sources.pop(sender_id, None)
            self._last_seen.pop(sender_id, None)

        if not self._sources:
            return None

        frames = []
        active_sources = 0
        for source in self._sources.values():
            if source:
                frames.append(source.popleft())
                active_sources += 1
            else:
                frames.append(b"\x00" * FRAME_BYTES)

        if active_sources == 0:
            return None
        return frames

    def _enqueue_packet(self, opus_payload):
        packet = f"MIXED|{self._seq}|".encode("ascii") + opus_payload
        self._seq = (self._seq + 1) & 0xFFFF
        try:
            self.mixed_queue.put_nowait(packet)
        except queue.Full:
            try:
                self.mixed_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.mixed_queue.put_nowait(packet)
            except queue.Full:
                pass

    def _mix_loop(self):
        frame_period = 0.020
        while self.running:
            start = time.perf_counter()
            with self._lock:
                frames = self._collect_frames_locked()

            if frames:
                mixed_pcm = _mix_pcm_frames(frames)
                try:
                    opus = self._encoder.encode(mixed_pcm)
                except Exception:
                    opus = b""
                if opus:
                    self._enqueue_packet(opus)

            elapsed = time.perf_counter() - start
            time.sleep(max(0.0, frame_period - elapsed))

    def start(self):
        if self.running:
            return
        self.running = True
        self._mix_thread = threading.Thread(
            target=self._mix_loop,
            daemon=True,
            name=f"personal-mix-{self.listener_id}",
        )
        self._mix_thread.start()

    def stop(self):
        self.running = False
        if self._mix_thread and self._mix_thread.is_alive():
            self._mix_thread.join(timeout=0.5)

    def drain_packets(self, limit=64):
        packets = []
        for _ in range(limit):
            try:
                packets.append(self.mixed_queue.get_nowait())
            except queue.Empty:
                break
        return packets
