import queue
import socket
import threading
import time
from collections import deque

import pyaudio

from native_mixer import mix_frames as native_mix_frames
from native_mixer import native_available
from native_ringbuffer import NativeRingBuffer
from native_ringbuffer import native_available as native_ring_available
from opus_codec import OpusCodec

RATE = 16000
FRAME = 320  # 20 ms @ 16 kHz
CHUNK = FRAME
FRAME_BYTES = FRAME * 2

DECODE_WORKERS = 2
DECODE_QUEUE_MAX = 512
OUTPUT_QUEUE_MAX = 8
RING_CAPACITY = 128
VAD_THRESHOLD = 35
VAD_HANGOVER_FRAMES = 20
RING_MAX_MISS_ADVANCE = 3


class PythonRingBuffer:
    def __init__(self, capacity):
        self.capacity = capacity
        self.frames = [None] * capacity
        self.seqs = [None] * capacity

    def push(self, seq, frame):
        idx = seq % self.capacity
        self.frames[idx] = frame
        self.seqs[idx] = seq

    def pop(self, seq):
        idx = seq % self.capacity
        if self.seqs[idx] != seq:
            return None
        frame = self.frames[idx]
        self.frames[idx] = None
        self.seqs[idx] = None
        return frame


class StreamState:
    def __init__(self, use_native_ring, frame_size):
        self.expected_seq = None
        self.legacy_frames = deque(maxlen=RING_CAPACITY)
        self.has_seq = False
        self.gain = 1.0
        self.miss_streak = 0

        self.native_ring = None
        self.py_ring = None

        if use_native_ring:
            self.native_ring = NativeRingBuffer(RING_CAPACITY, frame_size)
        else:
            self.py_ring = PythonRingBuffer(RING_CAPACITY)

    def close(self):
        if self.native_ring is not None:
            self.native_ring.close()

    def push(self, seq, frame):
        if seq is None:
            self.legacy_frames.append(frame)
            return

        self.has_seq = True
        if self.expected_seq is None:
            self.expected_seq = seq

        if self.native_ring is not None:
            self.native_ring.push(seq, frame)
        else:
            self.py_ring.push(seq, frame)

    def pop_for_mix(self):
        if self.has_seq and self.expected_seq is not None:
            seq = self.expected_seq
            if self.native_ring is not None:
                frame = self.native_ring.pop(seq)
            else:
                frame = self.py_ring.pop(seq)

            if frame is not None:
                self.miss_streak = 0
                self.expected_seq = (self.expected_seq + 1) & 0xFFFF
                return frame

            self.miss_streak += 1
            if self.miss_streak >= RING_MAX_MISS_ADVANCE:
                # Recover from packet loss/gaps without locking playback forever.
                self.miss_streak = 0
                self.expected_seq = (self.expected_seq + 1) & 0xFFFF

        if self.legacy_frames:
            return self.legacy_frames.popleft()

        return None


class AudioEngine:
    def __init__(self):
        self.client_id = None
        self.audio = pyaudio.PyAudio()

        self.tx_codec = OpusCodec(rate=RATE, channels=1, frame_size=FRAME)

        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        self.recv_sock.bind(("", 0))
        self.port = self.recv_sock.getsockname()[1]

        self.running = False
        self.send_thread = None  # Track the thread explicitly
        self.start_stop_lock = threading.Lock()
        self.tx_sock = None
        self.input = None

        self.state_lock = threading.Lock()
        self.hear_targets = set()
        self.stream_buffers = {}

        self.decode_queue = queue.Queue(maxsize=DECODE_QUEUE_MAX)
        self.output_queue = queue.Queue(maxsize=OUTPUT_QUEUE_MAX)

        self.tx_seq = 0
        self.tx_ts = 0
        self.last_played = b"\x00" * FRAME_BYTES
        self.last_vad_rms = 0.0
        self.last_vad_flag = 0
        self.vad_hangover = 0

        self.use_native_mixer = native_available()
        self.use_native_ring = native_ring_available()

        self.stats = {
            "recv_packets": 0,
            "recv_drops": 0,
            "recv_legacy": 0,
            "recv_vad_drop": 0,
            "recv_vad_marked": 0,
            "decode_packets": 0,
            "decode_fail": 0,
            "mixed_frames": 0,
            "mixed_sources": 0,
            "mixed_miss": 0,
            "callback_calls": 0,
            "callback_underrun": 0,
            "callback_time_s": 0.0,
            "native_mix_used": 0,
            "python_mix_used": 0,
            "tx_packets": 0,
            "tx_vad_voice": 0,
            "tx_vad_silence": 0,
        }

        print(
            "[AUDIO] Native mixer {}"
            .format("enabled" if self.use_native_mixer else "not found, using Python fallback")
        )
        print(
            "[AUDIO] Native ringbuffer {}"
            .format("enabled" if self.use_native_ring else "not found, using Python ring fallback")
        )

        self.output = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            output=True,
            frames_per_buffer=CHUNK,
            stream_callback=self._callback,
        )
        self.output.start_stream()

        threading.Thread(target=self.listen, daemon=True, name="audio-listen").start()
        for i in range(DECODE_WORKERS):
            threading.Thread(
                target=self.decode_worker,
                args=(i,),
                daemon=True,
                name=f"audio-decode-{i}",
            ).start()
        threading.Thread(target=self.mixer_loop, daemon=True, name="audio-mixer").start()

    def _new_stream_state(self):
        return StreamState(self.use_native_ring, FRAME)

    def set_hear_targets(self, targets):
        with self.state_lock:
            self.hear_targets = set(targets)
            for sid in list(self.stream_buffers.keys()):
                if sid not in self.hear_targets:
                    self.stream_buffers[sid].close()
                    del self.stream_buffers[sid]

    def _callback(self, in_data, frame_count, *_):
        del in_data
        start = time.perf_counter()

        wanted = frame_count * 2
        frame = b"\x00" * wanted
        try:
            frame = self.output_queue.get_nowait()
        except queue.Empty:
            self.stats["callback_underrun"] += 1

        if len(frame) != wanted:
            frame = (frame + (b"\x00" * wanted))[:wanted]

        self.last_played = frame

        self.stats["callback_calls"] += 1
        self.stats["callback_time_s"] += time.perf_counter() - start

        if self.stats["callback_calls"] % 1000 == 0:
            avg_ms = (self.stats["callback_time_s"] / self.stats["callback_calls"]) * 1000.0
            print(
                "[AUDIO] callback_avg_ms={:.4f} underrun={} recv={} decode={} mixed={}"
                .format(
                    avg_ms,
                    self.stats["callback_underrun"],
                    self.stats["recv_packets"],
                    self.stats["decode_packets"],
                    self.stats["mixed_frames"],
                )
            )

        return (frame, pyaudio.paContinue)

    def _push_decode_item(self, item):
        try:
            self.decode_queue.put_nowait(item)
        except queue.Full:
            self.stats["recv_drops"] += 1
            try:
                self.decode_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self.decode_queue.put_nowait(item)
            except queue.Full:
                self.stats["recv_drops"] += 1

    def _parse_audio_packet(self, data):
        # New format: sender|seq|timestamp|vad|opus_payload
        parts = data.split(b"|", 4)
        if len(parts) == 5:
            try:
                sender = parts[0].decode(errors="ignore").strip()
                seq = int(parts[1]) & 0xFFFF
                ts = int(parts[2])
                vad = int(parts[3])
                opus = parts[4]
                return sender, seq, ts, vad, opus, False
            except Exception:
                pass

        # Legacy format fallback: sender:opus_payload
        if b":" in data:
            sender, opus = data.split(b":", 1)
            sender_id = sender.decode(errors="ignore").strip()
            return sender_id, None, None, 1, opus, True

        return None, None, None, None, None, None

    def listen(self):
        print(f"[AUDIO] Listening for audio on port {self.port}")
        while True:
            try:
                data, addr = self.recv_sock.recvfrom(4096)
            except Exception as e:
                print(f"[AUDIO] recv_sock error: {e}")
                continue

            sender_id, seq, ts, vad, opus, legacy = self._parse_audio_packet(data)
            del ts

            if sender_id is None:
                print(f"[AUDIO] Malformed packet from {addr}: {data[:50]}")
                continue

            if self.client_id and sender_id == self.client_id:
                # Defensive guard: never play back our own stream if forwarded by mistake.
                continue

            self.stats["recv_packets"] += 1
            if legacy:
                self.stats["recv_legacy"] += 1

            if vad == 0:
                # Keep VAD as metadata only. Dropping here breaks seq continuity.
                self.stats["recv_vad_marked"] += 1

            self._push_decode_item((sender_id, opus, seq))

    def decode_worker(self, worker_id):
        codec = OpusCodec(rate=RATE, channels=1, frame_size=FRAME)
        while True:
            try:
                sender_id, opus, seq = self.decode_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                pcm = codec.decode(opus)
            except Exception as e:
                self.stats["decode_fail"] += 1
                print(f"[AUDIO] Decode worker {worker_id} error from {sender_id}: {e}")
                continue

            if not pcm:
                self.stats["decode_fail"] += 1
                continue

            with self.state_lock:
                state = self.stream_buffers.get(sender_id)
                if state is None:
                    state = self._new_stream_state()
                    self.stream_buffers[sender_id] = state
                state.push(seq, pcm)

            self.stats["decode_packets"] += 1

    def _mix_ready_frames(self):
        with self.state_lock:
            targets = tuple(self.hear_targets)
            frames = []
            gains = []
            for sid in targets:
                state = self.stream_buffers.get(sid)
                if not state:
                    continue

                frame = state.pop_for_mix()
                if frame is None:
                    self.stats["mixed_miss"] += 1
                    continue

                frames.append(frame)
                gains.append(state.gain)

        if not frames:
            return b"\x00" * FRAME_BYTES, 0

        if self.use_native_mixer:
            try:
                mixed = native_mix_frames(frames, gains, FRAME)
                self.stats["native_mix_used"] += 1
                return mixed, len(frames)
            except Exception as e:
                self.use_native_mixer = False
                print(f"[AUDIO] Native mixer failed, falling back to Python: {e}")

        accum = [0] * FRAME
        for frame, gain in zip(frames, gains):
            samples = memoryview(frame).cast("h")
            for i in range(FRAME):
                accum[i] += int(samples[i] * gain)

        mixed = bytearray(FRAME_BYTES)
        out = memoryview(mixed).cast("h")
        for i in range(FRAME):
            v = accum[i]
            if v > 32767:
                v = 32767
            elif v < -32768:
                v = -32768
            out[i] = v

        self.stats["python_mix_used"] += 1
        return bytes(mixed), len(frames)

    def _push_output_frame(self, frame):
        try:
            self.output_queue.put_nowait(frame)
        except queue.Full:
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self.output_queue.put_nowait(frame)
            except queue.Full:
                return

    def mixer_loop(self):
        frame_interval = FRAME / float(RATE)
        next_deadline = time.perf_counter()

        while True:
            frame, active = self._mix_ready_frames()
            self._push_output_frame(frame)

            self.stats["mixed_frames"] += 1
            self.stats["mixed_sources"] += active

            if self.stats["mixed_frames"] % 1000 == 0:
                avg_sources = self.stats["mixed_sources"] / max(1, self.stats["mixed_frames"])
                print(
                    "[AUDIO] mixed_frames={} avg_sources={:.2f} mixed_miss={} decode_queue={} output_queue={} native={} python={}"
                    .format(
                        self.stats["mixed_frames"],
                        avg_sources,
                        self.stats["mixed_miss"],
                        self.decode_queue.qsize(),
                        self.output_queue.qsize(),
                        self.stats["native_mix_used"],
                        self.stats["python_mix_used"],
                    )
                )

            next_deadline += frame_interval
            sleep_time = next_deadline - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_deadline = time.perf_counter()

    def _compute_vad(self, pcm_bytes):
        samples = memoryview(pcm_bytes).cast("h")
        if not samples:
            self.last_vad_rms = 0.0
            if self.vad_hangover > 0:
                self.vad_hangover -= 1
                self.last_vad_flag = 1
                return 1
            self.last_vad_flag = 0
            return 0
        energy = 0
        for s in samples:
            energy += s * s
        rms = (energy / len(samples)) ** 0.5
        if rms > VAD_THRESHOLD:
            self.vad_hangover = VAD_HANGOVER_FRAMES
            vad = 1
        elif self.vad_hangover > 0:
            self.vad_hangover -= 1
            vad = 1
        else:
            vad = 0
        self.last_vad_rms = rms
        self.last_vad_flag = vad
        return vad

    def start(self, server_ip):
        with self.start_stop_lock:
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

            self.tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.tx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)

        # Reset thread ref before starting new one
        self.send_thread = None

        def send():
            packet_count = 0
            next_status_log = time.monotonic() + 2.0
            while self.running:
                # REFINED: Check running BEFORE read() to exit faster on stop
                if not self.running:
                    break
                try:
                    pcm = self.input.read(CHUNK, exception_on_overflow=False)

                    if hasattr(self, "aec") and self.aec and hasattr(self, "last_played"):
                        try:
                            pcm = self.aec.process(pcm, self.last_played)
                        except Exception:
                            pass

                    opus = self.tx_codec.encode(pcm)
                    if not opus:
                        continue

                    seq = self.tx_seq
                    ts = self.tx_ts
                    vad = self._compute_vad(pcm)
                    if vad:
                        self.stats["tx_vad_voice"] += 1
                    else:
                        self.stats["tx_vad_silence"] += 1

                    header = f"{self.client_id}|{seq}|{ts}|{vad}|".encode()
                    packet = header + opus
                    if self.tx_sock is None:
                        continue
                    self.tx_sock.sendto(packet, (server_ip, 50002))

                    self.tx_seq = (self.tx_seq + 1) & 0xFFFF
                    self.tx_ts = (self.tx_ts + FRAME) & 0xFFFFFFFF

                    packet_count += 1
                    self.stats["tx_packets"] += 1
                    if packet_count % 100 == 0:
                        print(f"[AUDIO] Sent {packet_count} packets from {self.client_id}")

                    now = time.monotonic()
                    if now >= next_status_log:
                        print(
                            "[AUDIO] TX status active={} rms={:.1f} vad={} voice={} silence={} sent={}"
                            .format(
                                self.running,
                                self.last_vad_rms,
                                self.last_vad_flag,
                                self.stats["tx_vad_voice"],
                                self.stats["tx_vad_silence"],
                                self.stats["tx_packets"],
                            )
                        )
                        next_status_log = now + 2.0
                except Exception as e:
                    print(f"[AUDIO] Send error: {e}")
                    # Faster exit on fatal errors (e.g., socket closed)
                    if not self.running:
                        break
            print(f"[AUDIO] Send thread stopped for {self.client_id}")

        self.send_thread = threading.Thread(target=send, daemon=True, name="audio-send")
        self.send_thread.start()

    def stop(self):
        with self.start_stop_lock:
            self.running = False
            print(f"[AUDIO] Stopping capture for {self.client_id}...")  # DEBUG: Remove if too verbose
            if self.input is not None:
                try:
                    # REFINED: Stop stream FIRST to unblock any pending read()
                    self.input.stop_stream()
                    self.input.close()
                except Exception:
                    pass
                self.input = None
            if self.tx_sock is not None:
                try:
                    self.tx_sock.close()
                except Exception:
                    pass
                self.tx_sock = None

            # Wait for send thread to exit cleanly (prevents multiple threads)
            if self.send_thread is not None and self.send_thread.is_alive():
                self.send_thread.join(timeout=0.8)  # Slightly longer for safety, but quick
                if self.send_thread.is_alive():
                    print(f"[AUDIO] Send thread join timeout for {self.client_id} - forcing daemon exit")
            self.send_thread = None