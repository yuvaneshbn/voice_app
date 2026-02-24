# Voice App (LAN Intercom)

Voice App is a low-latency LAN voice intercom built with Python, PySide6, Opus, and a native C++ mixer.

## Current Architecture

- Server discovery: UDP broadcast on `50000`
- Control channel: TCP on `50001` (`REGISTER`, `JOIN`, `TARGETS`, `UNREGISTER`)
- Audio channel: UDP on `50002` (forwarded SFU-style)
- Server model: async room-based forwarding with directed targets
- Client model: local decode + jitter + native mix playback

## Features

- 1:1 and one-to-many voice routing
- Room-based conversation model with directed targets
- Selective hearing controls
- Opus encode/decode pipeline
- Jitter buffering for smoother playback
- Mandatory native mixer (`audio_native/native_mixer.dll`)

## Requirements

- Python 3.11+
- Windows 10/11
- Microphone + speaker/headphones
- LAN access for multi-device usage

## Repository Structure

- `client/` UI, audio pipeline, network control
- `server/` async control + UDP forwarding server
- `audio_native/` native mixer source + built DLL
- `opus/` Opus runtime DLL artifacts

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Native Mixer DLL (Required)

This project is configured to include `audio_native/native_mixer.dll` in git.

If you rebuild it:

```powershell
powershell -ExecutionPolicy Bypass -File audio_native\build_native.ps1
```

Client startup will fail if `native_mixer.dll` is missing.

## Run

Terminal 1 (server):

```powershell
cd server
python server.py
```

Terminal 2+ (clients):

```powershell
cd client
python main.py
```

## Ports / Firewall

- `50000/UDP` discovery
- `50001/TCP` control
- `50002/UDP` audio

Allow these ports on private network profiles.

## Build with PyInstaller

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

## Troubleshooting

- Discovery fails: enter server IP manually.
- No audio TX: confirm TALK targets are selected.
- No audio RX: confirm HEAR targets are enabled.
- Native mixer error: verify `audio_native/native_mixer.dll` exists.

## License

MIT. See `LICENSE`.
