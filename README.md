# Voice App (LAN Intercom)

Voice App is a Windows-focused LAN voice intercom system for low-latency communication.
It uses:

- UDP for audio media (`50002`)
- TCP for control signaling (`50001`)
- UDP discovery (`50000`)

The repository includes:

- A PySide6 desktop client
- A Python asyncio server with per-room audio mixing
- Native audio helper DLL integration (`native_mixer.dll`)
- Optional Windows QoS policy installer

## Table of Contents

1. Overview
2. Current Feature Set
3. Architecture
4. Repository Layout
5. Requirements
6. Setup (Source)
7. Run from Source
8. Client Workflow and UI
9. Protocol Reference
10. Audio Pipeline (Current Implementation)
11. QoS and DSCP
12. Build Native DLL
13. Build EXEs (PyInstaller)
14. Validation Checklist
15. Troubleshooting
16. Known Limitations
17. Development Notes
18. License

## 1. Overview

This is a room-based voice chat system:

- Clients capture mic PCM, encode with Opus, and send UDP packets to server.
- Server decodes each sender stream, mixes active speakers per room, re-encodes to Opus, and unicasts mixed audio to listeners in that room.
- Client UI controls who you talk to (`TARGETS`) and who you hear (`HEAR`).

The default room is `main`.

## 2. Current Feature Set

### Networking

- Server discovery over LAN
  - Server periodic broadcast: `VOICE_SERVER`
  - Client active probe packet: `VOICE_DISCOVER`
- Manual server IP entry fallback
- Client registration with shared secret
- Heartbeat (`PING`) and stale-client pruning
- Room join (`JOIN`) and participant listing (`LIST`)
- Directed talk targets and listener hear-filters

### Audio

- Opus codec, 16 kHz mono, 20 ms frame (`320` samples)
- One UDP media socket per client
- Server-side per-room real-time mixer (`RoomMixer`)
- Voice activity thresholding on server to reduce silent sources
- Client playback callback with packet loss concealment fallback
- Optional native echo cancellation (`native_mixer.dll`) support

### UI

- Qt `.ui` based forms loaded via `QUiLoader`
- Participant list with search filter
- Per-participant:
  - `Talk` checkbox
  - `Mute` checkbox
  - volume bar
  - mic status text
- Broadcast toggle (`Broadcast On`/`Broadcast Off`)
- Connection indicator and status bar
- Settings dialog:
  - input/output device selection
  - reconnect action
  - advanced audio controls (master volume, gain, noise suppression, echo toggle, mic test)

## 3. Architecture

### Client (`client/`)

- `main.py`
  - startup flow
  - server discovery and manual IP fallback
  - registration/join/reconnect
  - main window and settings dialog wiring
  - participant and routing state updates
- `audio.py`
  - mic capture/send loop
  - receive/decode/playback loop
  - device enumeration and selection
  - runtime audio controls
- `network.py`
  - discovery socket logic
- `opus_codec.py`
  - ctypes wrapper around `opus.dll`
- `echo_cancel.py` + `native_mixer.py`
  - native echo-cancel API wrapper (if DLL available)

### Server (`server/`)

- `server.py`
  - TCP control server (`50001`)
  - UDP media server (`50002`)
  - room membership and client state
  - per-room `RoomMixer` thread:
    - receives decoded PCM frames
    - mixes and applies scaling
    - encodes mixed Opus payloads
    - queues mixed packets for send loop
  - send loop applies `HEAR` filtering per listener

### Transport and Ports

- `50000/UDP` discovery
- `50001/TCP` control
- `50002/UDP` audio media

## 4. Repository Layout

```text
Two-way-switch1/
  audio_native/
    build_native.ps1
    CMakeLists.txt
    native_mixer.dll
    echo_cancel.cpp
    webrtc_apm.cpp
    webrtc_apm.h
  client/
    main.py
    audio.py
    network.py
    startup_dialog.py
    opus_codec.py
    native_mixer.py
    echo_cancel.py
    VoiceClient.spec
    technical-support.ico
    ui/
      main_window.ui
      participant_item.ui
      settings_dialog.ui
      volume_control.ui
    opus/
      opus.dll
    opus.dll
  server/
    server.py
    server.spec
    opus_codec.py
  tools/
    qos_policy_installer.py
    build_qos_installer.ps1
  docs/
    qos_policy.md
  opus/
    opus.dll
  requirements.txt
  README.md
```

## 5. Requirements

- Windows 10/11
- Python 3.11+
- Working microphone and speaker/headset
- LAN connectivity

Python dependencies:

```powershell
pip install -r requirements.txt
```

## 6. Setup (Source)

From repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Ensure runtime binaries exist:

- `audio_native/native_mixer.dll` (optional but recommended)
- Opus DLL in one of these locations:
  - `client/opus/opus.dll`
  - `client/opus.dll`
  - `opus/opus.dll`

## 7. Run from Source

### Start Server

```powershell
cd server
python server.py
```

Expected startup logs include:

- `Control TCP listening on port 50001`
- `Audio UDP listening on port 50002`

### Start Client

```powershell
cd client
python main.py
```

Startup sequence:

1. Client attempts discovery.
2. If not found, asks for manual server IP.
3. User enters unique client name.
4. Client sends `REGISTER` and `JOIN`.
5. Main window opens.

## 8. Client Workflow and UI

### Participant Routing

- `Talk` checked for one or more users: your mic stream is sent and routed to those targets.
- `Mute` checked on a participant: that participant is excluded from your local `HEAR` allow-list.
- `Broadcast On`: all other participants are auto-selected as targets.

### Connection and Heartbeat

- Client sends periodic `PING`.
- After consecutive failures, UI marks as disconnected.
- Settings dialog includes `Reconnect` to re-register/join quickly.

### Settings Device List Behavior

The app now prefers one host API family (WASAPI first) and de-duplicates generic aliases in the settings dropdown.
This reduces duplicate entries that represent the same physical device through multiple Windows audio APIs.

## 9. Protocol Reference

### Discovery

- Server broadcasts: `VOICE_SERVER` on UDP `50000`
- Client probes: `VOICE_DISCOVER` on UDP `50000`

### Control Commands (TCP `50001`)

- `REGISTER:<client_id>:<audio_port>[:<secret>]`
- `JOIN:<client_id>:<room_id>`
- `LIST` or `LIST:<client_id>`
- `PING:<client_id>`
- `TARGETS:<client_id>:<csv_targets>`
- `HEAR:<client_id>:<csv_targets>`
- `UNREGISTER:<client_id>`

Typical responses:

- `OK`
- `OK:<multicast_addr>` (legacy field retained by server response format)
- `TAKEN`
- `ERR`

### Registration Secret

Default secret currently used by code:

- Client: `VOICE_REGISTER_SECRET` env var (default `mysecret`)
- Server constant: `SERVER_SECRET = "mysecret"`

If you change one, update the other to match.

## 10. Audio Pipeline (Current Implementation)

### Client Transmit

1. Read PCM16 mono frame (`320` samples).
2. Apply UI-driven controls:
   - tx mute
   - gain and sensitivity shaping
   - optional noise gate
   - optional echo cancel capture path
3. Opus encode.
4. Send packet: `client_id|seq|timestamp:opus_payload`.

### Server Mix

1. Parse sender id and payload.
2. Decode sender Opus to PCM.
3. Feed sender PCM into room mixer.
4. Mixer thread combines active source frames every ~20 ms.
5. Re-encode mixed PCM to Opus as `MIXED|seq|opus`.
6. Send to room listeners, enforcing listener `HEAR` filters.

### Client Receive/Playback

1. Receive mixed packet.
2. Decode Opus to PCM.
3. Push frame into local receive queue.
4. Playback callback consumes queue and applies volume.
5. If queue underflows, uses Opus PLC (`decode(None)`) or silence fallback.

## 11. QoS and DSCP

Implemented DSCP markings:

- Audio UDP: EF (`46`)
- Control TCP + discovery: CS3 (`24`)

Windows QoS installer:

- Script: `tools/qos_policy_installer.py`
- Build helper: `tools/build_qos_installer.ps1`
- Output: `dist/VoiceQoSSetup.exe`

Install:

```powershell
.\dist\VoiceQoSSetup.exe
```

Remove:

```powershell
.\dist\VoiceQoSSetup.exe --remove
```

More details: `docs/qos_policy.md`.

## 12. Build Native DLL

```powershell
.\audio_native\build_native.ps1
```

CMake alternative:

```powershell
cmake -S audio_native -B audio_native\build
cmake --build audio_native\build --config Release
```

Expected output:

- `audio_native/native_mixer.dll`

## 13. Build EXEs (PyInstaller)

Install PyInstaller first:

```powershell
pip install pyinstaller
```

### Client EXE (recommended: spec file)

```powershell
cd client
pyinstaller --noconfirm --clean VoiceClient.spec
```

Output:

- `client/dist/VoiceClient.exe`

The spec already includes:

- `.ui` files
- app icon
- `opus.dll`
- `native_mixer.dll`
- `PySide6.QtUiTools` hidden import

### Server EXE (spec file)

```powershell
cd server
pyinstaller --noconfirm --clean server.spec
or
pyinstaller --onefile server.py
```

Output:

- `server/dist/server.exe`

The spec bundles `..\opus\opus.dll` into `opus/` for frozen runtime loading.

### Optional Direct Command (without spec)

Client:

```powershell
cd client
pyinstaller --noconfirm --clean --onefile --windowed `
  --name VoiceClient `
  --icon "technical-support.ico" `
  --hidden-import PySide6.QtUiTools `
  --add-data "ui;ui" `
  --add-data "technical-support.ico;." `
  --add-binary "opus.dll;opus" `
  --add-binary "native_mixer.dll;audio_native" `
  main.py
```

Server:

```powershell
cd server
pyinstaller --noconfirm --clean server.spec
```

## 14. Validation Checklist

1. Start server.
2. Start two or more clients.
3. Verify registration and participant list updates.
4. Toggle talk targets and confirm remote audio path changes.
5. Toggle participant mute and verify hear filtering.
6. Toggle broadcast and verify all-target selection behavior.
7. Verify reconnect button recovers after temporary disconnect.
8. Verify clean exit unregisters client.

## 15. Troubleshooting

### Settings shows too many input/output devices

Windows exposes the same hardware through multiple APIs (MME/DirectSound/WASAPI/WDM-KS). The app now de-duplicates and prefers WASAPI.
If you still see extra entries, use the WASAPI-labelled one.

### Server discovery fails

- Confirm server is running and port `50000/UDP` is open.
- Confirm local firewall allows UDP broadcast.
- Use manual server IP dialog.

### Registration fails with `TAKEN`

- Client name already exists.
- Choose a different name.

### No audio though connected

- Ensure at least one `Talk` target is selected.
- Ensure receiver has not muted sender.
- Confirm UDP `50002` is allowed by firewall/network policy.

### `opus.dll` not found

- Ensure DLL is present in one supported runtime path.
- For EXE builds, use provided `.spec` files.

### Echo cancel unavailable

- Check `audio_native/native_mixer.dll` exists and architecture matches Python interpreter.
- If unavailable, app still runs without native echo cancellation.

### Build fails because output EXE is in use

Stop running process, then rebuild.
Example for server port holder:

```powershell
netstat -aon | findstr :50001
taskkill /PID <PID> /F
```

## 16. Known Limitations

- Audio format is currently fixed to 16 kHz mono.
- Secret management is static by default (`mysecret`), suitable only for trusted LAN use unless hardened.
- No TLS/auth hardening on control channel.
- Legacy/generated `client/voice_ui.py` is not part of active runtime path.

## 17. Development Notes

- Keep client and server packet formats synchronized when changing headers.
- If you change control command parsing rules, update both UI flow and server handlers.
- When packaging, prefer `.spec` files to avoid missing asset regressions.

## 18. License

MIT License (`LICENSE`).
