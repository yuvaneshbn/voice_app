import ctypes
from ctypes import POINTER, byref, c_float, c_int, c_int16, c_void_p

from native_mixer import _dll as _native_dll


if _native_dll is not None:
    _native_dll.apm_create.argtypes = [c_int, c_int, c_int]
    _native_dll.apm_create.restype = c_void_p

    _native_dll.apm_destroy.argtypes = [c_void_p]
    _native_dll.apm_destroy.restype = None

    _native_dll.apm_config.argtypes = [c_void_p, c_int, c_int, c_int, c_int]
    _native_dll.apm_config.restype = c_int

    _native_dll.apm_set_delay_ms.argtypes = [c_void_p, c_int]
    _native_dll.apm_set_delay_ms.restype = c_int

    _native_dll.apm_process_reverse.argtypes = [c_void_p, POINTER(c_int16), c_int]
    _native_dll.apm_process_reverse.restype = c_int

    _native_dll.apm_process_capture.argtypes = [c_void_p, POINTER(c_int16), c_int, POINTER(c_int16)]
    _native_dll.apm_process_capture.restype = c_int

    _native_dll.apm_get_metrics.argtypes = [c_void_p, POINTER(c_float), POINTER(c_float), POINTER(c_int)]
    _native_dll.apm_get_metrics.restype = c_int


def apm_available():
    if _native_dll is None:
        return False
    return all(
        hasattr(_native_dll, name)
        for name in (
            "apm_create",
            "apm_destroy",
            "apm_config",
            "apm_set_delay_ms",
            "apm_process_reverse",
            "apm_process_capture",
            "apm_get_metrics",
        )
    )


class WebRTCApm:
    def __init__(self, sample_rate, channels, frame_size):
        if not apm_available():
            raise RuntimeError("WebRTC APM API is not available in native_mixer.dll")
        self._handle = _native_dll.apm_create(int(sample_rate), int(channels), int(frame_size))
        if not self._handle:
            raise RuntimeError("Failed to create native WebRTC APM instance")
        self.frame_size = int(frame_size)

    def close(self):
        if self._handle:
            _native_dll.apm_destroy(self._handle)
            self._handle = None

    def configure(self, enable_aec3=True, enable_ns=True, enable_agc=False, enable_vad=False):
        if not self._handle:
            return False
        rc = _native_dll.apm_config(
            self._handle,
            1 if enable_aec3 else 0,
            1 if enable_ns else 0,
            1 if enable_agc else 0,
            1 if enable_vad else 0,
        )
        return bool(rc)

    def set_delay_ms(self, delay_ms):
        if not self._handle:
            return False
        return bool(_native_dll.apm_set_delay_ms(self._handle, int(delay_ms)))

    def process_reverse(self, far_frame_bytes):
        if not self._handle or not far_frame_bytes:
            return False
        frame = (c_int16 * self.frame_size).from_buffer_copy(
            (far_frame_bytes + (b"\x00" * (self.frame_size * 2)))[: self.frame_size * 2]
        )
        return bool(_native_dll.apm_process_reverse(self._handle, frame, self.frame_size))

    def process_capture(self, near_frame_bytes):
        if not self._handle or not near_frame_bytes:
            return near_frame_bytes
        near = (c_int16 * self.frame_size).from_buffer_copy(
            (near_frame_bytes + (b"\x00" * (self.frame_size * 2)))[: self.frame_size * 2]
        )
        out = (c_int16 * self.frame_size)()
        ok = _native_dll.apm_process_capture(self._handle, near, self.frame_size, out)
        if not ok:
            return near_frame_bytes
        return ctypes.string_at(out, self.frame_size * 2)

    def get_metrics(self):
        if not self._handle:
            return None
        erl = c_float(0.0)
        erle = c_float(0.0)
        delay_ms = c_int(0)
        ok = _native_dll.apm_get_metrics(self._handle, byref(erl), byref(erle), byref(delay_ms))
        if not ok:
            return None
        return {
            "erl_db": float(erl.value),
            "erle_db": float(erle.value),
            "delay_ms": int(delay_ms.value),
        }
