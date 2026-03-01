# Voice App (LAN Low-Latency Intercom)

Voice App is a LAN voice communication system with hybrid transport design:

- UDP for real-time audio media
- TCP for control signaling

It is built for low-latency, room-based voice communication with direct target routing, native echo cancellation integration, adaptive jitter buffering, and QoS marking support.

## Table of Contents

1. Project Summary
2. Current Feature Set
3. Architecture
4. Traffic and QoS Policy
5. Repository Layout
6. Requirements
7. Setup
8. Running the App
9. Client UI Guide
10. Protocol Reference
11. Audio Pipeline Details
12. Windows QoS Policy Installer (One-Time EXE)
13. Building Native Components
14. Packaging (PyInstaller)
15. Validation Checklist
16. Troubleshooting
17. Development Notes
18. License

## 1. Project Summary

The project follows an SFU-style model:

- Server receives encoded UDP audio and forwards it.
- Clients capture, encode, send, receive, decode, and mix audio locally.
- TCP control commands manage registration, heartbeat, rooms, and targets.

This keeps the server lightweight and shifts media processing to clients.

## 2. Current Feature Set

### Core networking

- Auto server discovery (UDP broadcast) with manual IP fallback
- Client registration with unique client names
- Heartbeat-based liveness
- Room join support (default room: `main`)
- Directed talk targets (`TARGETS`) or room multicast fallback

### Audio and media

- Opus codec for real-time voice frames
- UDP audio transport (`50002`)
- Adaptive jitter buffering
- Packet-loss concealment path
- Per-stream level tracking and AGC-style normalization on playback
- Native echo cancellation integration through `native_mixer.dll`

### Client UI

- Qt `.ui` driven interface (`client/ui/*.ui`)
- Participant list with search
- Per participant controls:
  - `Talk` toggle
  - `Mute` toggle
  - mic status
  - volume meter
- Auto refresh participant list
- Broadcast toggle (`On/Off`)
- Active speaker status summary per client:
  - `Client X - talking`
  - `Client Y - listening`
- Local mic mute/unmute
- Settings dialog with:
  - input/output device selection
  - advanced audio controls
  - reconnect action

### Lifecycle behavior

- Default room is `main`
- Leave room action disconnects (`UNREGISTER`) and exits app
- Connection indicator shows connected/disconnected state

## 3. Architecture

### Components

- `server/server.py`
  - TCP control server on `50001`
  - UDP media forwarder on `50002`
  - room + client registry
- `client/main.py`
  - app startup, dialogs, UI wiring, control-plane commands
- `client/audio.py`
  - audio capture/playback, encode/decode, jitter/mix, send/receive
- `client/network.py`
  - discovery logic
- `audio_native/*`
  - native audio processing and echo cancellation bridge

### End-to-end flow

1. Client discovers server (`VOICE_SERVER` broadcast) or user enters server IP.
2. Client registers over TCP:
   - `REGISTER:<client_name>:<audio_port>:<secret>`
3. Client joins room:
   - `JOIN:<client_name>:main`
4. Client updates talk targets from UI:
   - `TARGETS:<client_name>:<csv_targets>`
5. Audio flows over UDP with packet header:
   - `sender|seq|timestamp:<opus_payload>`
6. On close/leave:
   - `UNREGISTER:<client_name>`

## 4. Traffic and QoS Policy

This project now marks traffic at socket level and supports Windows policy-based QoS.

### DSCP mapping

- Real-time audio (UDP): `DSCP 46` (`EF`)
- Control signaling (TCP): `DSCP 24` (`CS3`)
- Discovery traffic: treated as control class (`CS3`)

### In-code marking

Applied in:

- client control sockets (`main.py`, `startup_dialog.py`)
- client audio sockets (`audio.py`)
- server control sockets and listener (`server.py`)
- server audio forward/multicast sockets (`server.py`)

### Queueing/scheduling expectations on network devices

Marking alone is not enough. Switches/routers should be configured to:

1. Trust DSCP at access ports.
2. Map `EF (46)` to strict-priority voice queue.
3. Map `CS3 (24)` to a high-priority non-strict queue.
4. Reserve voice bandwidth budget (plan per active stream).
5. Police EF queue to prevent starvation of other classes.

## 5. Repository Layout

```text
Two-way-switch1/
  audio_native/
    native_mixer.dll
    echo_cancel.cpp
    webrtc_apm.cpp
    webrtc_apm.h
    CMakeLists.txt
    build_native.ps1
  client/
    main.py
    audio.py
    network.py
    startup_dialog.py
    opus_codec.py
    echo_cancel.py
    native_mixer.py
    ui/
      main_window.ui
      participant_item.ui
      settings_dialog.ui
      volume_control.ui
    opus/
      opus.dll
  server/
    server.py
  tools/
    qos_policy_installer.py
    build_qos_installer.ps1
  docs/
    qos_policy.md
  opus/
    opus.dll
  README.md
  requirements.txt
```

## 6. Requirements

- Windows 10/11 (primary target)
- Python 3.11+
- Working microphone and output audio device
- LAN connectivity between client and server

## 7. Setup

From repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Verify runtime native binaries exist:

- `audio_native/native_mixer.dll`
- `client/opus.dll` or `client/opus/opus.dll`

## 8. Running the App

### Start server

```powershell
cd server
python server.py
```

Expected startup lines:

- `Control TCP listening on port 50001`
- `Audio UDP listening on port 50002`

### Start client

```powershell
cd client
python main.py
```

At startup:

1. Server discovery runs.
2. If discovery fails, enter server IP manually.
3. Enter a unique client name.
4. Client registers and joins room `main`.

### Multi-client quick test

1. Start server.
2. Start client A (name example: `alpha`).
3. Start client B (name example: `bravo`).
4. In each client, enable `Talk` toward the other client (or use Broadcast).
5. Speak and verify two-way audio.

## 9. Client UI Guide

### Top toolbar

- Room combo: currently default `main`
- Leave Room: unregisters, disconnects, exits app
- Refresh List: manual participant refresh
- Connection indicator: connected/disconnected state

### Participants panel

- Search box filters participant list by name
- Each participant row shows:
  - name
  - `Talk` checkbox
  - `Mute` checkbox
  - mic status
  - volume progress bar

### Active speakers panel

- Shows per-client state lines:
  - `Client <name> - talking|listening`
- Speaker log list tracks speaking/stopped events
- System audio level bar indicates current local capture level

### My controls panel

- Master volume
- Gain
- Output volume
- Mic sensitivity
- Noise suppression
- Auto gain toggle
- Echo cancellation toggle
- Test mic button and mic level bar

### Bottom controls

- Mute Mic (self transmit mute)
- Broadcast toggle (`Broadcast On` / `Broadcast Off`)
- Settings button

### Settings dialog

- Audio devices
  - input device selection
  - output device selection
- Advanced audio
  - same control panel options
- Network
  - server IP display
  - reconnect action
- Save and Close / Cancel

## 10. Protocol Reference

### Ports

- `50000/UDP`: discovery
- `50001/TCP`: control
- `50002/UDP`: audio

### Commands

- `REGISTER:<client_id>:<audio_port>[:<secret>]`
- `JOIN:<client_id>:<room_id>`
- `LIST` or `LIST:<client_id>`
- `PING:<client_id>`
- `TARGETS:<client_id>:<csv_targets>`
- `UNREGISTER:<client_id>`

### Typical responses

- `OK`
- `OK:<multicast_addr>`
- `TAKEN`
- `ERR`

## 11. Audio Pipeline Details

### Capture and send

- Capture format: PCM16 mono @ 16 kHz
- Frame size: 320 samples (20 ms)
- Encoded with Opus
- Packetized with sequence and timestamp header

### Receive and playback

- Per-sender jitter buffer
- Adaptive target depth (`MIN/TARGET/MAX` frames)
- Missing-frame concealment and fast resync
- Per-stream level estimation and mixed output limiter

### Runtime safety

- Start/stop capture guarded with lock
- Send thread lifecycle checked to prevent duplicate starts
- Echo canceller access guarded with lock

## 12. Windows QoS Policy Installer (One-Time EXE)

The repository includes a one-time installer to create local policy-based QoS rules.

### Installer source

- `tools/qos_policy_installer.py`

### Build installer EXE

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_qos_installer.ps1
```

Output:

- `dist/VoiceQoSSetup.exe`

### Apply policies

Run as Administrator:

```powershell
.\dist\VoiceQoSSetup.exe
```

Creates:

- `Audio_Data`: UDP dst port `50002`, DSCP `46`
- `Audio_Control`: TCP dst port `50001`, DSCP `24`

Also runs:

```powershell
gpupdate /target:computer /force
```

### Remove policies

```powershell
.\dist\VoiceQoSSetup.exe --remove
```

## 13. Building Native Components

### Build native audio module

```powershell
.\audio_native\build_native.ps1
```

Alternative CMake flow:

```powershell
cmake -S audio_native -B audio_native\build
cmake --build audio_native\build --config Release
```

Expected artifact:

- `audio_native/native_mixer.dll`

## 14. Packaging (PyInstaller)

### Client executable

```powershell
cd client
pyinstaller --onefile --windowed `
  --add-binary "C:\path\to\client\opus\opus.dll;opus" `
  --add-binary "C:\path\to\audio_native\native_mixer.dll;audio_native" `
  main.py
```

### Server executable

```powershell
cd server
pyinstaller --onefile server.py
```

## 15. Validation Checklist

1. Start server and two clients.
2. Verify both clients appear in participant list automatically.
3. Toggle `Talk` and confirm target updates appear in server logs.
4. Toggle Broadcast on/off and verify targets update accordingly.
5. Check active speaker lines change between `talking` and `listening`.
6. Verify leave room unregisters and closes client.
7. Optional: verify DSCP with Wireshark.

## 16. Troubleshooting

### Discovery does not find server

- Ensure UDP `50000` is open in firewall.
- Try manual server IP in startup dialog.

### Registration fails (`TAKEN`)

- Name is already connected.
- Choose a different unique client name.

### No audio heard

- Verify `Talk` target is set or Broadcast is on.
- Ensure receiver has not muted sender.
- Confirm UDP `50002` is allowed.

### Frequent jitter/missing logs

- Check LAN congestion and Wi-Fi quality.
- Validate QoS trust and queue mapping on switches.
- Keep client and server on stable low-latency path.

### Echo issues

- Keep echo cancellation enabled.
- Prefer headset for best echo performance.
- Confirm `native_mixer.dll` is loaded and compatible.

### App exits unexpectedly

- Inspect `client/client_crash.log`.
- Verify Python/DLL architecture match (64-bit with 64-bit).

## 17. Development Notes

- Control plane uses TCP by design for reliability.
- Media plane uses UDP for low latency.
- Server does not decode media.
- Client owns decode, jitter, and mix complexity.

If protocol or packet format changes, update both server and client parsing/writing paths.

## 18. License

MIT License. See `LICENSE`.
