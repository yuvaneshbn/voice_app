# Voice App (LAN Low-Latency Intercom)

Voice App is a LAN voice communication system designed for low-latency, real-time communication.
It uses a hybrid transport model:

- UDP for real-time audio media
- TCP for control signaling

The project provides a desktop client (PySide6), a lightweight forwarding server, native audio processing integration, and QoS support for traffic prioritization.

## Table of Contents

1. Overview
2. Feature Summary
3. Architecture
4. QoS Design (Hybrid UDP/TCP)
5. Repository Structure
6. Requirements
7. Setup (Source)
8. Running Server and Client
9. Client UI Guide
10. Protocol Reference
11. Audio Pipeline Details
12. Build Native DLL
13. Build Windows QoS Installer EXE
14. Build Client EXE (PyInstaller)
15. Build Server EXE (PyInstaller)
16. Validation Checklist
17. Troubleshooting
18. Development Notes
19. License

## 1. Overview

Voice App follows an SFU-style design:

- Server receives encoded UDP audio packets and forwards them.
- Clients capture, encode, send, receive, decode, and mix audio locally.
- TCP controls client lifecycle and routing.

This keeps server CPU load low and enables horizontal scaling for LAN intercom scenarios.

## 2. Feature Summary

### Networking

- Auto-discovery of server over UDP broadcast
- Manual server IP fallback dialog
- Unique client name registration
- Room join support (default room: `main`)
- Directed voice routing via target list
- Heartbeat/liveness checks
- Graceful unregister on client exit

### Audio

- Opus codec on 20 ms frames at 16 kHz
- UDP media path on port `50002`
- Per-sender jitter buffering with adaptive depth
- Loss concealment/resync handling
- Per-stream leveling and mixed output limiting
- Native echo cancellation integration (`native_mixer.dll`)

### UI

- Qt Designer `.ui`-based main window and dialogs
- Searchable participant list
- Per-participant talk/mute controls
- Per-participant mic and level indicators
- Broadcast toggle (`On`/`Off`)
- Active speaker summary (`Client X - talking/listening`)
- Settings dialog for audio devices and advanced audio controls
- Connection status indicator

## 3. Architecture

### Core Components

- `server/server.py`
  - TCP control listener (`50001`)
  - UDP audio forwarder (`50002`)
  - Client registry, room registry, target routing
- `client/main.py`
  - App entry, startup flow, UI wiring, control commands
- `client/audio.py`
  - Capture, encode/decode, jitter, mix, send/receive
- `client/network.py`
  - Discovery broadcast handling
- `audio_native/*`
  - Native echo/audio processing bridge and build files

### End-to-End Flow

1. Client discovers server (`VOICE_SERVER`) or prompts for manual IP.
2. Client registers over TCP.
3. Client joins room `main` and receives multicast group info.
4. Client updates talk targets from UI actions.
5. Audio packets flow over UDP and are forwarded by server.
6. Client leaves room or exits, then unregisters.

## 4. QoS Design (Hybrid UDP/TCP)

### Classification and Marking

- Real-time audio (UDP): DSCP `46` (`EF`)
- Control signaling (TCP): DSCP `24` (`CS3`)
- Discovery traffic: treated as control class (`CS3`)

Socket-level DSCP marking is implemented in client and server code paths.

### Queueing Recommendations (Network Devices)

Marking alone is not sufficient. Switches/routers should:

1. Trust DSCP at access ports.
2. Map `EF (46)` to strict priority queue.
3. Map `CS3 (24)` to high-priority non-strict queue.
4. Reserve bandwidth for active voice streams.
5. Police EF queue to avoid starvation.

See also: `docs/qos_policy.md`

## 5. Repository Structure

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
    technical-support.ico
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

- Windows 10/11
- Python 3.11+
- Functional microphone and speaker/headset
- LAN connectivity for clients/server

## 7. Setup (Source)

From repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Required runtime binaries:

- `audio_native/native_mixer.dll`
- `client/opus.dll` or `client/opus/opus.dll`

## 8. Running Server and Client

### Start Server

```powershell
cd server
python server.py
```

Expected logs:

- `Control TCP listening on port 50001`
- `Audio UDP listening on port 50002`

### Start Client

```powershell
cd client
python main.py
```

Startup steps:

1. Server discovery.
2. Manual IP prompt if needed.
3. Unique client name entry.
4. Registration + join room `main`.
5. Main window opens.

## 9. Client UI Guide

### Top Toolbar

- Room (default `main`)
- Leave Room (unregister + exit)
- Refresh List
- Connected/Disconnected indicator

### Participants

- Search input filters participants
- Row fields:
  - client name
  - `Talk` checkbox
  - `Mute` checkbox
  - mic state
  - volume bar

### Active Speakers

- Status lines like:
  - `Client alpha - talking`
  - `Client bravo - listening`
- Speaker log list
- System level bar

### My Controls

- Master volume
- Gain
- Output level
- Mic sensitivity
- Noise suppression
- Auto gain
- Echo cancellation
- Test mic

### Bottom Controls

- Mute mic
- Broadcast toggle
- Settings

## 10. Protocol Reference

### Ports

- `50000/UDP` discovery
- `50001/TCP` control
- `50002/UDP` media

### Control Commands

- `REGISTER:<client_id>:<audio_port>[:<secret>]`
- `JOIN:<client_id>:<room_id>`
- `LIST` or `LIST:<client_id>`
- `PING:<client_id>`
- `TARGETS:<client_id>:<csv_targets>`
- `UNREGISTER:<client_id>`

### Common Responses

- `OK`
- `OK:<multicast_addr>`
- `TAKEN`
- `ERR`

## 11. Audio Pipeline Details

### Transmit

- PCM16 mono, 16 kHz
- Frame size: 320 samples (20 ms)
- Opus encode + sequence/timestamp header

### Receive

- Per-sender jitter buffers
- Adaptive jitter target
- Gap handling and quick resync
- Mixed output with soft clipping

### Safety

- Capture start/stop protected by lock
- Send thread lifecycle guards
- Echo processing guarded to avoid race crashes

## 12. Build Native DLL

```powershell
.\audio_native\build_native.ps1
```

Alternative:

```powershell
cmake -S audio_native -B audio_native\build
cmake --build audio_native\build --config Release
```

Expected output:

- `audio_native/native_mixer.dll`

## 13. Build Windows QoS Installer EXE

Build:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_qos_installer.ps1
```

Output:

- `dist/VoiceQoSSetup.exe`

Use:

```powershell
.\dist\VoiceQoSSetup.exe
```

Remove policies:

```powershell
.\dist\VoiceQoSSetup.exe --remove
```

## 14. Build Client EXE (PyInstaller)

The client UI uses external `.ui` files. If you do not package `client/ui`, the EXE may show startup dialog only and fail to open the main window.

### Debug Build (recommended first)

Run from `client` folder:

```powershell
pyinstaller --noconfirm --clean --onefile --console `
  --name VoiceClient `
  --icon "technical-support.ico" `
  --hidden-import PySide6.QtUiTools `
  --add-data "ui;ui" `
  --add-data "technical-support.ico;." `
  --add-binary "opus.dll;opus" `
  --add-binary "native_mixer.dll;audio_native" `
  main.py
```

Test:

```powershell
.\dist\VoiceClient.exe
```

### Final GUI Build

```powershell
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

## 15. Build Server EXE (PyInstaller)

From `server` folder:

```powershell
pyinstaller --noconfirm --clean server.spec
```

This spec bundles Opus for frozen builds (`..\opus\opus.dll -> opus\opus.dll`), so `server.exe` can start without requiring a separate local DLL copy.

## 16. Validation Checklist

1. Start server and two clients.
2. Verify both clients appear automatically in participant list.
3. Verify talk target changes in server logs.
4. Verify broadcast toggle updates targets correctly.
5. Verify active speaker lines update (`talking`/`listening`).
6. Verify leave room unregisters and exits.
7. Verify QoS policies and DSCP values (optional, Wireshark).

## 17. Troubleshooting

### Startup dialog appears but main window does not open in EXE

- Cause: `.ui` files were not packaged.
- Fix: include `--add-data "ui;ui"` and `--hidden-import PySide6.QtUiTools` in PyInstaller command.

### Registration fails (`TAKEN`)

- Client name already in use.
- Choose another unique name.

### No audio

- Check `Talk` routing or Broadcast state.
- Ensure receiver has not muted sender.
- Confirm firewall allows UDP `50002`.

### Server EXE fails with `Could not find opus.dll`

- Cause: EXE was built without bundling Opus runtime DLL.
- Fix: build from `server` folder using:
  - `pyinstaller --noconfirm --clean server.spec`

### Discovery fails

- Ensure UDP `50000` is open.
- Use manual server IP entry.

### Jitter/missing sequence logs are frequent

- Check LAN quality, congestion, and Wi-Fi stability.
- Validate switch/router QoS trust and queue policy.

### Echo issues or random audio crashes

- Prefer headset during testing.
- Verify `native_mixer.dll` architecture matches Python (x64/x64).
- Check `client/client_crash.log` for native stack errors.

### Window icon not shown

- Ensure `technical-support.ico` is included in build.
- For PyInstaller, include:
  - `--icon "technical-support.ico"`
  - `--add-data "technical-support.ico;."`

## 18. Development Notes

- Control plane is TCP for reliability.
- Media plane is UDP for latency.
- Server does not decode media.
- Client handles decode/jitter/mix complexity.

When changing protocol or packet format, update both client and server parsing and writer logic.

## 19. License

MIT License. See `LICENSE`.
