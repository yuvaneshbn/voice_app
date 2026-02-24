import ctypes
from ctypes import c_int, c_void_p, c_ubyte, POINTER, c_short
import os

# Load opus DLL; allow relative path or same folder as executable
dll_path = os.path.join(os.path.dirname(__file__), "opus", "opus.dll")

# Try several candidate locations for opus.dll so apps and PyInstaller builds work
candidates = [
    dll_path,
    os.path.join(os.path.dirname(__file__), "opus.dll"),
    os.path.join(os.getcwd(), "opus.dll"),
    os.path.join(os.path.dirname(__file__), "..", "opus", "opus.dll"),
    "opus.dll",
]

last_exc = None
for p in candidates:
    try:
        opus = ctypes.CDLL(p)
        break
    except OSError as e:
        last_exc = e
else:
    msg = (
        "Could not find opus.dll. Tried the following locations:\n"
        + "\n".join(candidates)
        + "\n\nPlease place a Windows build of libopus as 'opus.dll' in one of those locations,\n"
        + "or add it to your PATH. You can obtain Windows builds from the Opus releases: https://github.com/xiph/opus/releases"
    )
    raise FileNotFoundError(msg) from last_exc

OPUS_APPLICATION_VOIP = 2048
OPUS_OK = 0
OPUS_SET_BITRATE_REQUEST = 4002
OPUS_SET_COMPLEXITY_REQUEST = 4010
OPUS_SET_INBAND_FEC_REQUEST = 4012
OPUS_SET_PACKET_LOSS_PERC_REQUEST = 4014
OPUS_SET_DTX_REQUEST = 4016

opus.opus_encoder_create.restype = c_void_p
opus.opus_encoder_create.argtypes = [c_int, c_int, c_int, POINTER(c_int)]

opus.opus_encode.restype = c_int
opus.opus_encode.argtypes = [
    c_void_p,
    POINTER(c_short),
    c_int,
    POINTER(c_ubyte),
    c_int,
]

opus.opus_decoder_create.restype = c_void_p
opus.opus_decoder_create.argtypes = [c_int, c_int, POINTER(c_int)]

opus.opus_decode.restype = c_int
opus.opus_decode.argtypes = [
    c_void_p,
    POINTER(c_ubyte),
    c_int,
    POINTER(c_short),
    c_int,
    c_int,
]

opus.opus_encoder_ctl.restype = c_int
opus.opus_encoder_ctl.argtypes = [c_void_p, c_int, c_int]


class OpusCodec:
    """Minimal Opus wrapper tuned for 20ms @ 16 kHz, mono.

    encode(pcm_bytes) -> bytes (encoded payload)
    decode(opus_bytes) -> pcm bytes (16-bit little-endian)
    """

    def __init__(
        self,
        rate=16000,
        channels=1,
        frame_size=320,
        enable_fec=False,
        packet_loss_perc=0,
        bitrate=16000,
        complexity=10,
        enable_dtx=True,
        create_encoder=True,
        create_decoder=True,
    ):
        self.frame_size = frame_size
        self.encoder = None
        self.decoder = None

        err = c_int()
        if create_encoder:
            self.encoder = opus.opus_encoder_create(
                rate, channels, OPUS_APPLICATION_VOIP, ctypes.byref(err)
            )
            if not self.encoder:
                raise RuntimeError("Opus encoder creation failed")

            if bitrate > 0:
                self._set_encoder_ctl_int(OPUS_SET_BITRATE_REQUEST, bitrate)
            if complexity >= 0:
                self._set_encoder_ctl_int(OPUS_SET_COMPLEXITY_REQUEST, complexity)
            self._set_encoder_ctl_int(OPUS_SET_DTX_REQUEST, 1 if enable_dtx else 0)
            self._set_encoder_ctl_int(OPUS_SET_INBAND_FEC_REQUEST, 1 if enable_fec else 0)
            if packet_loss_perc > 0:
                self._set_encoder_ctl_int(OPUS_SET_PACKET_LOSS_PERC_REQUEST, packet_loss_perc)

        if create_decoder:
            self.decoder = opus.opus_decoder_create(rate, channels, ctypes.byref(err))
            if not self.decoder:
                raise RuntimeError("Opus decoder creation failed")

    def _set_encoder_ctl_int(self, request, value):
        if not self.encoder:
            return
        try:
            rc = opus.opus_encoder_ctl(self.encoder, int(request), int(value))
            if rc != OPUS_OK:
                print(f"[OPUS] encoder_ctl request={request} value={value} rc={rc}")
        except Exception as e:
            print(f"[OPUS] encoder_ctl unavailable request={request}: {e}")

    def encode(self, pcm_bytes):
        if not self.encoder:
            return b""
        # Expect exactly frame_size * 2 bytes (int16)
        if len(pcm_bytes) != self.frame_size * 2:
            # pad or trim to frame size
            pcm_bytes = (pcm_bytes + b"\x00" * (self.frame_size * 2))[: self.frame_size * 2]

        pcm = (c_short * self.frame_size).from_buffer_copy(pcm_bytes)
        out = (c_ubyte * 4000)()
        size = opus.opus_encode(self.encoder, pcm, self.frame_size, out, 4000)
        if size < 0:
            return b""
        return bytes(out[:size])

    def decode(self, opus_bytes):
        if not self.decoder:
            return b""
        pcm = (c_short * self.frame_size)()
        if opus_bytes:
            buf = (c_ubyte * len(opus_bytes)).from_buffer_copy(opus_bytes)
            n = opus.opus_decode(self.decoder, buf, len(opus_bytes), pcm, self.frame_size, 0)
        else:
            # packet loss concealment: pass NULL buffer with len 0
            n = opus.opus_decode(self.decoder, None, 0, pcm, self.frame_size, 0)

        if n < 0:
            return b""

        # n = number of samples decoded (per channel). Build bytes (int16 little-endian)
        byte_count = n * 2
        return ctypes.string_at(ctypes.addressof(pcm), byte_count)
