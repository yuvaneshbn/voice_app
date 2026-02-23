import ctypes
import os
from ctypes import c_int, c_int16, c_uint16, c_void_p


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
    _dll.ringbuffer_create.argtypes = [c_int, c_int]
    _dll.ringbuffer_create.restype = c_void_p

    _dll.ringbuffer_destroy.argtypes = [c_void_p]
    _dll.ringbuffer_destroy.restype = None

    _dll.ringbuffer_push.argtypes = [c_void_p, c_uint16, ctypes.POINTER(c_int16)]
    _dll.ringbuffer_push.restype = None

    _dll.ringbuffer_pop.argtypes = [c_void_p, c_uint16, ctypes.POINTER(c_int16)]
    _dll.ringbuffer_pop.restype = c_int


def native_available():
    return _dll is not None


class NativeRingBuffer:
    def __init__(self, capacity, frame_size):
        if _dll is None:
            raise RuntimeError("native_mixer.dll not available")
        self.frame_size = frame_size
        self.handle = _dll.ringbuffer_create(capacity, frame_size)
        if not self.handle:
            raise RuntimeError("ringbuffer_create failed")

    def close(self):
        if self.handle:
            _dll.ringbuffer_destroy(self.handle)
            self.handle = None

    def __del__(self):
        self.close()

    def push(self, seq, frame_bytes):
        if not self.handle:
            return
        padded = (frame_bytes + (b"\x00" * (self.frame_size * 2)))[: self.frame_size * 2]
        frame_arr = (c_int16 * self.frame_size).from_buffer_copy(padded)
        _dll.ringbuffer_push(self.handle, c_uint16(seq & 0xFFFF), frame_arr)

    def pop(self, seq):
        if not self.handle:
            return None
        out = (c_int16 * self.frame_size)()
        ok = _dll.ringbuffer_pop(self.handle, c_uint16(seq & 0xFFFF), out)
        if not ok:
            return None
        return ctypes.string_at(out, self.frame_size * 2)
