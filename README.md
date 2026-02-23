# Voice App (LAN Intercom)

Low-latency LAN voice intercom with selective talk/hear controls, Opus encoding, and optional native C++ mixer acceleration.

## Features

- 1:1 or one-to-many voice routing (private talk and broadcast)
- Selective hearing controls per client
- UDP LAN server discovery
- Opus voice codec integration
- Jitter buffer playback pipeline
- Optional native C++ mixer (`audio_native/native_mixer.dll`)

## Architecture

- `server/` acts as UDP forwarder (SFU-style relay; no audio mixing on server)
- `client/` handles capture, VAD tagging, Opus encode/decode, jitter buffering, and playback mixing

## Requirements

- Python 3.11 recommended
- Windows (tested), LAN connectivity between clients and server
- Audio input/output devices

## Setup

```powershell
pip install -r requirements.txt
```

## Run

Start server:

```powershell
cd server
python server.py
```

Start client:

```powershell
cd client
python main.py
```

## Network Ports

- `50000` discovery
- `50001` control
- `50002` audio RTP-like packets

Allow these UDP ports in firewall on server/client machines.

## Native Mixer (Optional, Recommended)

Build native mixer DLL:

```powershell
powershell -ExecutionPolicy Bypass -File audio_native\build_native.ps1
```

Generated output:

- `audio_native\native_mixer.dll`

Runtime behavior:

- If DLL is found, client uses native mixer.
- If DLL is missing, client falls back to Python mixer.

## Opus DLL Notes (Windows)

Place `opus.dll` at:

- `client\opus\opus.dll` (recommended)

Quick verification:

```powershell
python -c "from client.opus_codec import OpusCodec; print('opus OK')"
```

## PyInstaller

Client:

```powershell
cd client
pyinstaller main.spec
```

Server:

```powershell
cd server
pyinstaller server.spec
```

One-file client example with explicit binaries:

```powershell
pyinstaller --onefile --windowed --add-binary "C:\full\path\to\opus.dll;opus" --add-binary "C:\full\path\to\native_mixer.dll;audio_native" client\main.py
```

## Troubleshooting

- No audio: check Windows input/output device selection and levels
- Echo: disable `Listen to this device` in Windows microphone properties
- Discovery fails: enter server IP manually
- Packet loss/choppy playback: increase jitter fill in `client/audio.py`

## Repository Info (GitHub About)

Use this in GitHub repo settings -> About:

- Description: `LAN voice intercom with Opus codec, jitter buffer, and optional native C++ mixer`
- Website: `https://github.com/yuvaneshbn/voice_app`
- Topics: `voip, udp, opus, pyside6, audio, lan, intercom, realtime-audio, python`

## License

This project is licensed under the MIT License. See `LICENSE`.
