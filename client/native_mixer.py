import ctypes
import os
from ctypes import POINTER, c_float, c_int, c_int16


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


def native_available():
    return _dll is not None


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
