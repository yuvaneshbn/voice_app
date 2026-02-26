import ctypes
import os


def _load_native_dll():
    here = os.path.dirname(__file__)
    candidates = [
        os.path.join(here, "audio_native", "native_mixer.dll"),
        os.path.join(here, "..", "audio_native", "native_mixer.dll"),
        os.path.join(here, "..", "audio_native", "build", "Release", "native_mixer.dll"),
        os.path.join(here, "..", "audio_native", "build", "native_mixer.dll"),
        os.path.join(os.getcwd(), "native_mixer.dll"),
    ]

    for path in candidates:
        norm = os.path.normpath(path)
        if os.path.exists(norm):
            return ctypes.CDLL(norm)
    return None


_dll = _load_native_dll()


def native_available():
    return _dll is not None
