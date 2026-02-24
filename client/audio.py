import queue
import socket
import threading
import time
from collections import deque
import os

import pyaudio

from native_mixer import mix_frames as native_mix_frames
from native_mixer import native_available
from opus_codec import OpusCodec

RATE = 16000
FRAME = 320  # 20 ms @ 16 kHz (quality-first profile)
CHUNK = FRAME
FRAME_BYTES = FRAME * 2

DECODE_WORKERS = max(4, (os.cpu_count() or 8) // 2)
DECODE_QUEUE_MAX = 2048
OUTPUT_QUEUE_MAX = 48
INPUT_QUEUE_MAX = 128

JITTER_TARGET_FILL = 10
JITTER_MAX_SIZE = 256
JITTER_TARGET_MIN = 8
JITTER_TARGET_MAX = 14

VAD_THRESHOLD = 35
VAD_HANGOVER_FRAMES = 20
NOISE_GATE_RMS = 70.0
NOISE_GATE_ATTACK_RMS = 180.0
DC_BLOCK_R = 0.995
ECHO_ATTENUATE_GAIN = 0.65
ECHO_SUPPRESS_MIN_RMS = 300.0
OPUS_TX_ENABLE_FEC = True
OPUS_TX_PACKET_LOSS_PERC = 10
OPUS_TX_BITRATE = 32000
OPUS_TX_COMPLEXITY = 10
PLC_DECAY = 0.85
UNDERRUN_DECAY = 0.90
GATE_MIN_GAIN = 0.08
GATE_ATTACK = 0.35
GATE_RELEASE = 0.05
ENABLE_ECHO_SUPPRESS = False
ENABLE_LOWPASS_SMOOTH = False


def _seq_diff(a, b):
    # Signed 16-bit sequence distance in range [-32768, 32767].
    return ((a - b + 32768) & 0xFFFF) - 32768


class JitterBuffer:
    def __init__(self, target_fill=JITTER_TARGET_FILL, max_size=JITTER_MAX_SIZE):
        self.target_fill = target_fill
        self.max_size = max_size
        self.buffer = {}  # seq -> frame bytes
        self.expected_seq = None

    def push(self, seq, frame):
        if seq is None:
            return

        seq = seq & 0xFFFF
        if self.expected_seq is None:
            self.expected_seq = seq

        # Drop frames that are too old relative to expected sequence.
        if _seq_diff(seq, self.expected_seq) < -self.max_size:
            return

        self.buffer[seq] = frame

        while len(self.buffer) > self.max_size:
            # Drop farthest old frame first relative to expected_seq.
            oldest = min(self.buffer.keys(), key=lambda s: _seq_diff(s, self.expected_seq))
            del self.buffer[oldest]

    def pop(self):
        if self.expected_seq is None:
            return None

        seq = self.expected_seq
        if seq in self.buffer:
            frame = self.buffer.pop(seq)
            self.expected_seq = (self.expected_seq + 1) & 0xFFFF
            return frame

        if len(self.buffer) < self.target_fill:
            return None

        # Buffer has enough frames but expected is missing: assume loss and resync.
        future = [s for s in self.buffer.keys() if _seq_diff(s, self.expected_seq) >= 0]
        if future:
            next_seq = min(future, key=lambda s: _seq_diff(s, self.expected_seq))
            self.expected_seq = next_seq
            frame = self.buffer.pop(next_seq)
            self.expected_seq = (self.expected_seq + 1) & 0xFFFF
            return frame

        # No future frame found; move one step and let mixer output silence.
        self.expected_seq = (self.expected_seq + 1) & 0xFFFF
        return None

    def close(self):
        self.buffer.clear()


class StreamState:
    def __init__(self, frame_size, target_fill):
        del frame_size
        self.jitter_buffer = JitterBuffer(target_fill=target_fill, max_size=JITTER_MAX_SIZE)
        self.legacy_frames = deque(maxlen=JITTER_MAX_SIZE)
        self.gain = 1.0
        self.last_frame = None
        self.plc_active = False

    def close(self):
        self.jitter_buffer.close()

    def push(self, seq, frame):
        if seq is None:
            self.legacy_frames.append(frame)
            return
        self.jitter_buffer.push(seq, frame)

    def pop_for_mix(self):
        frame = self.jitter_buffer.pop()
        if frame is not None:
            if self.last_frame is not None and self.plc_active:
                old = memoryview(self.last_frame).cast("h")
                new = memoryview(frame).cast("h")
                blended = bytearray(len(frame))
                out = memoryview(blended).cast("h")
                for i in range(len(out)):
                    out[i] = int(old[i] * 0.3 + new[i] * 0.7)
                self.last_frame = bytes(blended)
                self.plc_active = False
                return self.last_frame
            self.last_frame = frame
            self.plc_active = False
            return frame
        if self.legacy_frames:
            frame = self.legacy_frames.popleft()
            self.last_frame = frame
            self.plc_active = False
            return frame
        if self.last_frame:
            plc = bytearray(self.last_frame)
            samples = memoryview(plc).cast("h")
            for i in range(len(samples)):
                samples[i] = int(samples[i] * PLC_DECAY)
            self.last_frame = bytes(plc)
            self.plc_active = True
            return self.last_frame
        return None


class AudioEngine:
    def __init__(self):
        self.client_id = None
        self.audio = pyaudio.PyAudio()

        self.tx_codec = OpusCodec(
            rate=RATE,
            channels=1,
            frame_size=FRAME,
            enable_fec=OPUS_TX_ENABLE_FEC,
            packet_loss_perc=OPUS_TX_PACKET_LOSS_PERC,
            bitrate=OPUS_TX_BITRATE,
            complexity=OPUS_TX_COMPLEXITY,
            enable_dtx=True,
        )

        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        self.recv_sock.bind(("", 0))
        self.port = self.recv_sock.getsockname()[1]

        self.running = False
        self.start_stop_lock = threading.Lock()
        self.send_thread = None
        self.tx_sock = None
        self._tx_sock = None
        self.input = None
        self._send_generation = 0

        self.state_lock = threading.Lock()
        self.hear_targets = set()
        self.stream_buffers = {}

        self.decode_queue = queue.Queue(maxsize=DECODE_QUEUE_MAX)
        self.output_queue = queue.Queue(maxsize=OUTPUT_QUEUE_MAX)
        self.input_queue = queue.Queue(maxsize=INPUT_QUEUE_MAX)

        self.tx_seq = 0
        self.tx_ts = 0
        self.last_played = b"\x00" * FRAME_BYTES
        self.last_vad_rms = 0.0
        self.last_vad_flag = 0
        self.vad_hangover = 0
        self._dc_prev_x = 0.0
        self._dc_prev_y = 0.0
        self._lp_prev = 0.0
        self._noise_floor_ema = 55.0
        self._gate_gain = 1.0
        self.dynamic_jitter_target = JITTER_TARGET_FILL
        self._adapt_prev_mixed = 0
        self._adapt_prev_miss = 0
        self._adapt_prev_callback = 0
        self._adapt_prev_underrun = 0

        if not native_available():
            raise RuntimeError("native_mixer.dll not found. It is required for audio mixing.")
        self.use_native_mixer = True

        self.stats = {
            "recv_packets": 0,
            "recv_drops": 0,
            "recv_legacy": 0,
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
            "input_queue_drops": 0,
        }

        print("[AUDIO] Native mixer enabled")

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
        return StreamState(FRAME, self.dynamic_jitter_target)

    def set_hear_targets(self, targets):
        with self.state_lock:
            self.hear_targets = set(targets)
            for sid in list(self.stream_buffers.keys()):
                if sid not in self.hear_targets:
                    self.stream_buffers[sid].close()
                    del self.stream_buffers[sid]

    def _callback(self, in_data, frame_count, *_):
        try:
            del in_data
            start = time.perf_counter()

            wanted = frame_count * 2
            frame = b"\x00" * wanted
            try:
                frame = self.output_queue.get_nowait()
            except queue.Empty:
                self.stats["callback_underrun"] += 1
                if len(self.last_played) == wanted:
                    degraded = bytearray(self.last_played)
                    samples = memoryview(degraded).cast("h")
                    for i in range(len(samples)):
                        samples[i] = int(samples[i] * UNDERRUN_DECAY)
                    frame = bytes(degraded)

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
        except Exception as e:
            print(f"[AUDIO] Output callback error: {e}")
            return (b"\x00" * (frame_count * 2), pyaudio.paContinue)

    def _input_callback(self, in_data, frame_count, time_info, status):
        try:
            del frame_count, time_info, status
            if in_data is None:
                return (None, pyaudio.paContinue)
            try:
                self.input_queue.put_nowait(in_data)
            except queue.Full:
                self.stats["input_queue_drops"] += 1
                try:
                    self.input_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.input_queue.put_nowait(in_data)
                except queue.Full:
                    self.stats["input_queue_drops"] += 1
            return (None, pyaudio.paContinue)
        except Exception as e:
            print(f"[AUDIO] Input callback error: {e}")
            return (None, pyaudio.paContinue)

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
                continue

            self.stats["recv_packets"] += 1
            if legacy:
                self.stats["recv_legacy"] += 1
            if vad == 0:
                self.stats["recv_vad_marked"] += 1

            self._push_decode_item((sender_id, opus, seq))

    def decode_worker(self, worker_id):
        codec = OpusCodec(
            rate=RATE,
            channels=1,
            frame_size=FRAME,
            create_encoder=False,
            create_decoder=True,
        )
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

        mixed = native_mix_frames(frames, gains, FRAME)
        self.stats["native_mix_used"] += 1
        return mixed, len(frames)

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
            self._adapt_jitter_target()

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

    @staticmethod
    def _clamp_i16(v):
        if v > 32767:
            return 32767
        if v < -32768:
            return -32768
        return v

    @staticmethod
    def _rms_from_samples(samples):
        if not samples:
            return 0.0
        energy = 0
        for s in samples:
            energy += s * s
        return (energy / len(samples)) ** 0.5

    def _update_noise_floor(self, rms):
        # Track a stable background floor; react slower upward than downward.
        alpha_up = 0.005
        alpha_down = 0.02
        if rms > self._noise_floor_ema:
            self._noise_floor_ema += (rms - self._noise_floor_ema) * alpha_up
        else:
            self._noise_floor_ema += (rms - self._noise_floor_ema) * alpha_down

    def _preprocess_capture(self, pcm_bytes):
        # Conservative processing for lower disturbance without heavy dependencies:
        # 1) suppress speaker leakage using current playback frame
        # 2) remove DC/low rumble with a one-pole DC blocker
        # 3) simple noise gate on very low-level background noise
        mic_out = bytearray(pcm_bytes)
        mic = memoryview(mic_out).cast("h")

        if ENABLE_ECHO_SUPPRESS and len(self.last_played) == len(pcm_bytes):
            ref = memoryview(self.last_played).cast("h")
            ref_rms = self._rms_from_samples(ref)
            mic_rms = self._rms_from_samples(mic)
            if ref_rms >= ECHO_SUPPRESS_MIN_RMS and ref_rms > (mic_rms * 0.8):
                for i in range(FRAME):
                    mic[i] = self._clamp_i16(int(mic[i] * ECHO_ATTENUATE_GAIN))

        prev_x = self._dc_prev_x
        prev_y = self._dc_prev_y
        for i in range(FRAME):
            x = float(mic[i])
            y = x - prev_x + (DC_BLOCK_R * prev_y)
            prev_x = x
            prev_y = y
            mic[i] = self._clamp_i16(int(y))
        self._dc_prev_x = prev_x
        self._dc_prev_y = prev_y

        if ENABLE_LOWPASS_SMOOTH:
            # One-pole low-pass smoothing to suppress high-frequency hiss.
            prev = self._lp_prev
            for i in range(FRAME):
                cur = float(mic[i])
                smoothed = (0.6 * prev) + (0.4 * cur)
                mic[i] = self._clamp_i16(int(smoothed))
                prev = smoothed
            self._lp_prev = prev

        rms = self._rms_from_samples(mic)
        self._update_noise_floor(rms)
        dynamic_floor = max(NOISE_GATE_RMS, self._noise_floor_ema * 1.8)

        # Soft gate envelope avoids hard on/off chopping and click noise.
        open_thr = max(NOISE_GATE_ATTACK_RMS, dynamic_floor * 1.6)
        close_thr = dynamic_floor
        if rms >= open_thr:
            desired_gain = 1.0
        elif rms <= close_thr:
            desired_gain = GATE_MIN_GAIN
        else:
            ratio = (rms - close_thr) / max(1.0, open_thr - close_thr)
            desired_gain = GATE_MIN_GAIN + ((1.0 - GATE_MIN_GAIN) * ratio)

        if desired_gain > self._gate_gain:
            self._gate_gain += (desired_gain - self._gate_gain) * GATE_ATTACK
        else:
            self._gate_gain += (desired_gain - self._gate_gain) * GATE_RELEASE

        if self._gate_gain < 0.999:
            for i in range(FRAME):
                mic[i] = self._clamp_i16(int(mic[i] * self._gate_gain))

        return bytes(mic_out)

    def _adapt_jitter_target(self):
        mixed_now = self.stats["mixed_frames"]
        window = mixed_now - self._adapt_prev_mixed
        if window < 200:
            return

        miss_now = self.stats["mixed_miss"]
        cb_now = self.stats["callback_calls"]
        underrun_now = self.stats["callback_underrun"]

        miss_delta = miss_now - self._adapt_prev_miss
        cb_delta = cb_now - self._adapt_prev_callback
        underrun_delta = underrun_now - self._adapt_prev_underrun
        underrun_rate = underrun_delta / max(1, cb_delta)

        new_target = self.dynamic_jitter_target
        if underrun_rate > 0.05 or miss_delta > int(window * 0.60):
            new_target = min(JITTER_TARGET_MAX, new_target + 1)
        elif underrun_rate < 0.01 and miss_delta < int(window * 0.15):
            new_target = max(JITTER_TARGET_MIN, new_target - 1)

        if new_target != self.dynamic_jitter_target:
            self.dynamic_jitter_target = new_target
            with self.state_lock:
                for state in self.stream_buffers.values():
                    state.jitter_buffer.target_fill = new_target
            print(f"[AUDIO] Adaptive jitter target -> {new_target}")

        self._adapt_prev_mixed = mixed_now
        self._adapt_prev_miss = miss_now
        self._adapt_prev_callback = cb_now
        self._adapt_prev_underrun = underrun_now

    def start(self, server_ip):
        with self.start_stop_lock:
            if self.running or not self.client_id:
                return

            while not self.input_queue.empty():
                try:
                    self.input_queue.get_nowait()
                except queue.Empty:
                    break

            self._send_generation += 1
            generation = self._send_generation
            self.running = True
            print(f"[AUDIO] Audio capture ACTIVE for {self.client_id} -> {server_ip}:50002")

            self.input = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
                stream_callback=self._input_callback,
            )
            self.input.start_stream()

            self.tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.tx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
            self._tx_sock = self.tx_sock

        def send(thread_generation):
            packet_count = 0
            next_status_log = time.monotonic() + 2.0
            while True:
                with self.start_stop_lock:
                    if not self.running or self._send_generation != thread_generation:
                        break
                    input_stream = self.input
                    sock = self._tx_sock
                if input_stream is None:
                    time.sleep(0.01)
                    continue

                try:
                    try:
                        pcm = self.input_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    if len(pcm) != FRAME_BYTES:
                        pcm = (pcm + (b"\x00" * FRAME_BYTES))[:FRAME_BYTES]

                    pcm = self._preprocess_capture(pcm)

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

                    if sock is None:
                        continue

                    try:
                        sock.sendto(packet, (server_ip, 50002))
                    except OSError as e:
                        if not self.running:
                            break
                        print(f"[AUDIO] Send error: {e}")
                        continue

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
                    with self.start_stop_lock:
                        if not self.running or self._send_generation != thread_generation:
                            break

            print(f"[AUDIO] Send thread stopped for {self.client_id} (gen={thread_generation})")

        self.send_thread = threading.Thread(target=send, args=(generation,), daemon=True, name="audio-send")
        self.send_thread.start()

    def stop(self):
        input_stream = None
        tx_sock = None
        thread = None

        with self.start_stop_lock:
            self._send_generation += 1
            self.running = False
            input_stream = self.input
            self.input = None
            tx_sock = self.tx_sock
            self.tx_sock = None
            self._tx_sock = None
            thread = self.send_thread
            self.send_thread = None

        if input_stream is not None:
            try:
                input_stream.stop_stream()
                input_stream.close()
            except Exception as e:
                print(f"[AUDIO] Input close error: {e}")

        if tx_sock is not None:
            try:
                tx_sock.close()
            except Exception as e:
                print(f"[AUDIO] Socket close error: {e}")

        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
            if thread.is_alive():
                print(f"[AUDIO] Warning: send thread still alive for {self.client_id}")
