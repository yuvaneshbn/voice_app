import ctypes
import os
from ctypes import POINTER, c_float, c_int, c_int16, c_void_p


def _load_native_dll():
    here = os.path.dirname(__file__)
    candidates = [
        os.path.join(here, "audio_native", "native_mixer.dll"),
        os.path.join(here, "..", "audio_native", "native_mixer.dll"),
        os.path.join(here, "..", "audio_native", "build", "native_mixer.dll"),
        os.path.join(os.getcwd(), "native_mixer.dll"),
    ]

    for path in candidates:
        norm = os.path.normpath(path)
        if os.path.exists(norm):
            return ctypes.CDLL(norm)
    return None


_dll = _load_native_dll()

if _dll is not None:
    _dll.mix_frames.argtypes = [
        POINTER(POINTER(c_int16)),
        POINTER(c_float),
        c_int,
        c_int,
        POINTER(c_int16),
    ]
    _dll.mix_frames.restype = None

    if hasattr(_dll, "agc_create"):
        _dll.agc_create.argtypes = [c_float]
        _dll.agc_create.restype = c_void_p
        _dll.agc_destroy.argtypes = [c_void_p]
        _dll.agc_destroy.restype = None
        _dll.agc_process.argtypes = [c_void_p, POINTER(c_int16), c_int]
        _dll.agc_process.restype = c_float


def native_available():
    return _dll is not None


def agc_available():
    if _dll is None:
        return False
    return all(hasattr(_dll, name) for name in ("agc_create", "agc_destroy", "agc_process"))


class SimpleAGC:
    def __init__(self, targetRMS=3000.0):
        if not agc_available():
            raise RuntimeError("SimpleAGC API is not available in native_mixer.dll")
        self._handle = _dll.agc_create(float(targetRMS))
        if not self._handle:
            raise RuntimeError("Failed to create native SimpleAGC instance")

    def close(self):
        if self._handle:
            _dll.agc_destroy(self._handle)
            self._handle = None

    def process(self, samples, frame_size):
        if not self._handle:
            return 1.0
        frame_size = int(frame_size)
        try:
            return float(_dll.agc_process(self._handle, samples, frame_size))
        except TypeError:
            # Accept common Python buffer types and coerce to int16* for DLL call.
            if isinstance(samples, bytearray):
                arr = (c_int16 * frame_size).from_buffer(samples)
            elif isinstance(samples, (bytes, memoryview)):
                raw = bytes(samples)
                arr = (c_int16 * frame_size).from_buffer_copy(
                    (raw + (b"\x00" * (frame_size * 2)))[: frame_size * 2]
                )
            else:
                raise
            return float(_dll.agc_process(self._handle, arr, frame_size))


def mix_frames(frames, gains, frame_size):
    if not frames:
        return b"\x00" * (frame_size * 2)

    count = len(frames)
    in_ptrs = (POINTER(c_int16) * count)()
    gains_arr = (c_float * count)()
    frame_refs = []

    for i, frame in enumerate(frames):
        padded = (frame + (b"\x00" * (frame_size * 2)))[: frame_size * 2]
        arr = (c_int16 * frame_size).from_buffer_copy(padded)
        frame_refs.append(arr)
        in_ptrs[i] = ctypes.cast(arr, POINTER(c_int16))
        gains_arr[i] = float(gains[i]) if i < len(gains) else 1.0

    out_arr = (c_int16 * frame_size)()
    _dll.mix_frames(in_ptrs, gains_arr, count, frame_size, out_arr)
    return ctypes.string_at(out_arr, frame_size * 2)
