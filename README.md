# Voice App (LAN Intercom)

Voice App is a low-latency LAN voice communication system for private networks. It provides real-time push-style voice routing between clients with selective talk/hear controls, server discovery, and a native C++ mixer for playback performance.

The current implementation is optimized for LAN use and supports:

- Automatic server discovery on local network
- Client registration with unique IDs
- Directed voice routing (choose who you talk to)
- Room-based fallback routing on server
- Local jitter buffering and decoding on clients
- Required native mixer for stable multi-stream playback

## Table of Contents

1. Overview
2. How the System Works
3. Repository Structure
4. Prerequisites
5. Installation
6. Native Mixer Requirement
7. Running the Application
8. Network Ports and Firewall
9. Protocol Reference
10. Audio Pipeline Details
11. Build Executables
12. Performance and Scaling Notes
13. Troubleshooting
14. Development Notes
15. License

## 1. Overview

This project uses an SFU-style model:

- The server does not decode or mix audio.
- Each client captures audio, encodes with Opus, sends UDP packets to server.
- Server forwards packets to selected targets.
- Receiving clients decode and mix locally.

This design keeps server CPU usage low and shifts decode/mix load to clients.

## 2. How the System Works

### Discovery

- Server broadcasts `VOICE_SERVER` on UDP port `50000`.
- Clients listen on `50000` and auto-detect the server IP.
- If discovery fails, user can enter server IP manually.

### Control Plane (TCP)

- Port: `50001`
- Transport: TCP
- Commands are newline-terminated text.

Client control sequence:

1. `REGISTER:<client_id>:<audio_port>`
2. `JOIN:<client_id>:main`
3. `TARGETS:<client_id>:<csv_target_ids>` whenever UI TALK changes
4. `UNREGISTER:<client_id>` on app close

### Audio Plane (UDP)

- Port: `50002`
- Transport: UDP
- Packet format:

`sender|seq|timestamp|vad|<opus_payload>`

Server extracts sender ID and forwards packet to:

- explicit `targets` if set
- otherwise room members (excluding sender)

## 3. Repository Structure

```text
Two-way-switch/
  audio_native/
    native_mixer.dll        # required at runtime
    *.cpp, *.h              # native mixer source
    build_native.ps1        # build script
  client/
    main.py                 # Qt app entry + control logic
    audio.py                # capture/encode/decode/jitter/mix pipeline
    native_mixer.py         # ctypes bridge to native_mixer.dll
    network.py              # discovery logic
    startup_dialog.py       # startup/server dialogs
    voice_ui.py, voice.ui   # generated UI + source UI
    opus_codec.py           # Opus wrapper
  server/
    server.py               # async TCP control + UDP forwarder
  opus/
    opus.dll / import libs  # Opus artifacts
  requirements.txt
  README.md
```

## 4. Prerequisites

- OS: Windows 10/11
- Python: 3.11 recommended
- Audio devices: microphone + speakers/headphones
- Network: same LAN/subnet for auto-discovery

## 5. Installation

From project root:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## 6. Native Mixer Requirement

Native mixer is mandatory in current implementation.

Required file:

- `audio_native\native_mixer.dll`

If missing, client raises a startup error and exits.

### Build/Rebuild Native Mixer

```powershell
.\audio_native\build_native.ps1
```

If script execution is restricted on your machine, use:

```powershell
cmake -S audio_native -B audio_native\build
cmake --build audio_native\build --config Release
```

AEC3 is vendored at `audio_native\third_party\AEC3` and is required for build.

After build, confirm the DLL exists at:

- `audio_native\native_mixer.dll`

## 7. Running the Application

Open separate terminals.

### Start Server

```powershell
cd server
python server.py
```

Expected logs include:

- `Control TCP listening on port 50001`
- `Audio UDP listening on port 50002`

### Start Client(s)

```powershell
cd client
python main.py
```

For each client:

- Choose a unique client ID (for example `1`, `2`, `3`, `4`)
- Verify registration success in logs
- Use TALK buttons to select targets
- Use HEAR buttons to filter incoming streams

### Basic 2-Client Test

1. Start server.
2. Start Client 1 with ID `1`.
3. Start Client 2 with ID `2`.
4. In Client 1, enable TALK to `2`.
5. In Client 2, enable TALK to `1`.
6. Speak and verify two-way audio.

## 8. Network Ports and Firewall

Allow these ports for private network profile.

- `50000/UDP` discovery
- `50001/TCP` control
- `50002/UDP` audio

If discovery fails but direct connection works, firewall/broadcast restrictions are likely blocking UDP broadcast.

## 9. Protocol Reference

### Server Responses

- `OK`
- `TAKEN`
- `ERR`

### Control Commands

#### REGISTER

`REGISTER:<client_id>:<audio_port>`

Registers client ID with server using TCP peer IP + declared UDP audio port.

#### JOIN

`JOIN:<client_id>:<room_id>`

Moves client to a room. Default room in client flow is `main`.

#### TARGETS

`TARGETS:<client_id>:<id1,id2,id3>`

Sets directed targets for forwarding.

- Empty target list means no directed targets.
- Server may then use room fallback behavior.

#### UNREGISTER

`UNREGISTER:<client_id>`

Removes client from registry and room.

## 10. Audio Pipeline Details

### Capture and Transmit

- Frame size: `320` samples (`20ms @ 16kHz`)
- Encoding: Opus
- Includes sequence number + timestamp + VAD flag
- TX socket send buffer increased for burst tolerance

### Receive and Decode

- UDP receive buffer increased (`SO_RCVBUF`)
- Decode workers run in parallel
- Decode queue and output queue are enlarged for load stability

### Jitter and Mix

- Per-sender jitter buffer
- Resync logic for missing sequence frames
- Playback mixing uses native C++ DLL only

### Runtime Safety

- Start/stop synchronized by lock
- Send thread lifecycle guarded to prevent stale thread races
- On stop: stream/socket teardown and bounded thread join

## 11. Build Executables

### Server

```powershell
cd server
pyinstaller server.spec
```

### Client

```powershell
cd client
pyinstaller main.spec
```

### Client (one-file command with required DLLs)

```powershell
pyinstaller --onefile --windowed `
  --add-binary "C:\Users\YUVANESH\Desktop\projects\Two-way-switch\client\opus\opus.dll;opus" `
  --add-binary "C:\Users\YUVANESH\Desktop\projects\Two-way-switch\audio_native\native_mixer.dll;audio_native" `
  main.py
```

## 12. Performance and Scaling Notes

Current tuning targets improved stability beyond very small group calls.

Implemented improvements include:

- Async control/audio handling on server
- Larger socket buffers on server/client
- Higher decode worker count
- Increased jitter and queue sizes
- Native mixer requirement for predictable mixing cost

Practical scaling still depends on:

- CPU class of server and clients
- LAN quality/packet loss
- Number of concurrent active speakers

## 13. Troubleshooting

### Client closes unexpectedly

- Run client from terminal and inspect traceback.
- Confirm `audio_native\native_mixer.dll` exists.
- Verify matching Python/Opus/DLL architecture (64-bit vs 64-bit).

### One-way audio

- Ensure both clients selected each other in TALK targets.
- Confirm server receives `TARGETS` updates.
- Check firewall rules for UDP `50002`.

### Discovery fails

- Enter server IP manually in dialog.
- Verify UDP `50000` not blocked.
- Check that server is running and broadcasting.

### Registration fails (`TAKEN`)

- Client ID already in use.
- Choose a different ID or ensure old client is unregistered.

### No incoming audio

- Check HEAR buttons for expected senders.
- Validate server logs show forwarding activity.

### Malformed packet logs on server

- Occasional invalid UDP payloads can happen on noisy LAN.
- Persistent high rate may indicate non-client traffic hitting port `50002`.

## 14. Development Notes

- Control channel uses TCP by design for reliability.
- Audio channel remains UDP for low latency.
- Server keeps routing logic simple; no audio decode/mix server-side.
- Client owns decode, jitter, and playback mixing complexity.

For protocol changes, update both:

- `server/server.py` control parser and router
- `client/main.py` control sender logic
- `client/audio.py` packet writer/parser if audio header changes

## 15. License

MIT License. See `LICENSE`.
