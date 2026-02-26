import ctypes
from ctypes import POINTER, byref, c_float, c_int, c_int16, c_void_p

from native_mixer import _dll as _native_dll


if _native_dll is not None and all(
    hasattr(_native_dll, name)
    for name in (
        "ec_create",
        "ec_destroy",
        "ec_set_delay_ms",
        "ec_process_reverse",
        "ec_process_capture",
        "ec_get_metrics",
    )
):
    _native_dll.ec_create.argtypes = [c_int, c_int, c_int]
    _native_dll.ec_create.restype = c_void_p

    _native_dll.ec_destroy.argtypes = [c_void_p]
    _native_dll.ec_destroy.restype = None

    _native_dll.ec_set_delay_ms.argtypes = [c_void_p, c_int]
    _native_dll.ec_set_delay_ms.restype = c_int

    _native_dll.ec_process_reverse.argtypes = [c_void_p, POINTER(c_int16), c_int]
    _native_dll.ec_process_reverse.restype = c_int

    _native_dll.ec_process_capture.argtypes = [c_void_p, POINTER(c_int16), c_int, POINTER(c_int16)]
    _native_dll.ec_process_capture.restype = c_int

    _native_dll.ec_get_metrics.argtypes = [c_void_p, POINTER(c_float), POINTER(c_float), POINTER(c_int)]
    _native_dll.ec_get_metrics.restype = c_int


def echo_cancel_available():
    if _native_dll is None:
        return False
    return all(
        hasattr(_native_dll, name)
        for name in (
            "ec_create",
            "ec_destroy",
            "ec_set_delay_ms",
            "ec_process_reverse",
            "ec_process_capture",
            "ec_get_metrics",
        )
    )


class EchoCanceller:
    def __init__(self, sample_rate, channels, frame_size, delay_ms=60):
        if not echo_cancel_available():
            raise RuntimeError("Native echo cancel API is not available in native_mixer.dll")
        self.frame_size = int(frame_size)
        self._handle = _native_dll.ec_create(int(sample_rate), int(channels), self.frame_size)
        if not self._handle:
            raise RuntimeError("Failed to create native echo canceller")
        _native_dll.ec_set_delay_ms(self._handle, int(delay_ms))

    def close(self):
        if self._handle:
            _native_dll.ec_destroy(self._handle)
            self._handle = None

    def set_delay_ms(self, delay_ms):
        if not self._handle:
            return False
        return bool(_native_dll.ec_set_delay_ms(self._handle, int(delay_ms)))

    def process_reverse(self, far_frame_bytes):
        if not self._handle or not far_frame_bytes:
            return False
        frame = (c_int16 * self.frame_size).from_buffer_copy(
            (far_frame_bytes + (b"\x00" * (self.frame_size * 2)))[: self.frame_size * 2]
        )
        return bool(_native_dll.ec_process_reverse(self._handle, frame, self.frame_size))

    def process_capture(self, near_frame_bytes):
        if not self._handle or not near_frame_bytes:
            return near_frame_bytes
        near = (c_int16 * self.frame_size).from_buffer_copy(
            (near_frame_bytes + (b"\x00" * (self.frame_size * 2)))[: self.frame_size * 2]
        )
        out = (c_int16 * self.frame_size)()
        ok = _native_dll.ec_process_capture(self._handle, near, self.frame_size, out)
        if not ok:
            return near_frame_bytes
        return ctypes.string_at(out, self.frame_size * 2)

    def get_metrics(self):
        if not self._handle:
            return None
        erl = c_float(0.0)
        erle = c_float(0.0)
        delay_ms = c_int(0)
        ok = _native_dll.ec_get_metrics(self._handle, byref(erl), byref(erle), byref(delay_ms))
        if not ok:
            return None
        return {
            "erl_db": float(erl.value),
            "erle_db": float(erle.value),
            "delay_ms": int(delay_ms.value),
        }
