# Voice App (LAN Intercom)

Voice App is a low-latency LAN voice intercom system built with Python, PySide6, UDP sockets, and Opus.
It supports selective talking/hearing between clients, broadcast mode, server auto-discovery, and an optional native C++ mixer for better performance.

## What This Project Does

- Runs a lightweight UDP server that forwards audio between clients
- Lets each client choose who to talk to (`TALK` buttons)
- Lets each client choose who to hear (`HEAR` buttons)
- Supports broadcast-to-all from the client UI
- Uses Opus codec for compressed real-time voice
- Uses jitter buffering on receive path to improve playback stability

## Features

- 1:1 and one-to-many voice routing
- Selective hearing controls
- UDP LAN discovery
- Opus encode/decode pipeline
- Jitter buffer playback pipeline
- Optional native C++ audio mixer (`audio_native/native_mixer.dll`)

## Architecture

- `server/`: SFU-style forwarder (no decoding/mixing)
- `client/`: capture, optional AEC hook, VAD flagging, Opus encode/decode, jitter buffering, playback mix
- `audio_native/`: native mixer source/build files

## Requirements

- Python 3.11 recommended
- Windows 10/11 (tested)
- Microphone + speaker/headphone
- LAN connectivity (same subnet) for multi-device usage

## Repository Structure

- `client/` client app UI and audio pipeline
- `server/` server discovery + control + audio forwarding
- `audio_native/` native mixer C++ module and build script
- `opus/` Opus DLL artifacts
- `requirements.txt` Python dependencies
- `README.md` documentation

## Installation

### 1. Clone the repository

```powershell
git clone https://github.com/yuvaneshbn/voice_app.git
cd voice_app
```

### 2. Create and activate a virtual environment (recommended)

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

### 3. Install Python dependencies

```powershell
pip install -r requirements.txt
```

### 4. Verify `opus.dll` availability

Recommended location:

- `client\opus\opus.dll`

Quick check:

```powershell
python -c "from client.opus_codec import OpusCodec; print('opus OK')"
```

If this fails, place a matching-bitness `opus.dll` in `client\opus\`.

## Running the App

## Quick Start (Single Machine Test)

Open 3 terminals from project root:

1. Terminal A (server)

```powershell
cd server
python server.py
```

2. Terminal B (client 1)

```powershell
cd client
python main.py
```

3. Terminal C (client 2)

```powershell
cd client
python main.py
```

In clients:

- Select unique IDs (for example `1` and `2`)
- On client 1, select target `2` in TALK row
- On client 2, select target `1` in TALK row
- Optionally use center TALK button for broadcast mode

## LAN Multi-PC Run

1. Start server on one machine:

```powershell
cd server
python server.py
```

2. Start client on each machine:

```powershell
cd client
python main.py
```

3. If discovery does not find server automatically, enter server IP manually when prompted.

## Ports and Firewall

Allow these UDP ports on all participating machines:

- `50000` discovery
- `50001` control
- `50002` audio

On Windows Defender Firewall, add inbound UDP rules for these ports (or allow Python executable for private network).

## Native Mixer (Optional, Recommended)

Build native mixer DLL:

```powershell
powershell -ExecutionPolicy Bypass -File audio_native\build_native.ps1
```

Generated file:

- `audio_native\native_mixer.dll`

Runtime behavior:

- If DLL exists, client uses native mixer automatically
- If DLL is missing, client falls back to Python mixer

## Build Executables (PyInstaller)

### Client

```powershell
cd client
pyinstaller main.spec
```

### Server

```powershell
cd server
pyinstaller server.spec
```

### One-file client example with explicit binaries

```powershell
pyinstaller --onefile --windowed --add-binary "C:\full\path\to\opus.dll;opus" --add-binary "C:\full\path\to\native_mixer.dll;audio_native" client\main.py
```

## Runtime Tips

- Use headphones to avoid acoustic feedback
- Keep microphone gain moderate to reduce clipping/echo
- If call audio is choppy, tune jitter params in `client/audio.py`:
  - `JITTER_TARGET_FILL`
  - `JITTER_MAX_SIZE`

## Troubleshooting

- No audio output:
  - Verify output device in Windows sound settings
  - Check client HEAR buttons
- No audio transmission:
  - Verify TALK target selection
  - Confirm mic permission for Python app
- Echo:
  - Disable `Listen to this device` in Windows mic properties
  - Use headphones during testing
- Discovery fails:
  - Enter server IP manually
  - Confirm UDP 50000 is allowed
- Opus load error:
  - Confirm `client\opus\opus.dll` exists
  - Check 64-bit Python vs 64-bit DLL compatibility


## License

This project is licensed under the MIT License. See `LICENSE`.
