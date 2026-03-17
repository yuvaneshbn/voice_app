# OYX

OYX is a Windows LAN voice communication system with:

- a Qt desktop client (`voice_client`)
- a C++ server (`voice_server`)
- a shared Opus-based audio transport layer
- an optional native echo cancellation DLL built from a vendored WebRTC AEC3 tree

The project is designed for low-latency voice on a local network. Clients discover the server over broadcast, register over TCP, and stream compressed audio over UDP. The server mixes room audio and sends per-listener mixes back to each client.

## Current Scope

This repository currently targets Windows. The codebase depends on:

- WinSock / Win32 APIs
- Qt 6 Widgets
- PortAudio loaded dynamically at runtime
- prebuilt Opus binaries in `third_party/opus`

The default user flow is:

1. Start `voice_server`
2. Start one or more `voice_client` instances
3. Let the client auto-discover the server, or enter the server IP manually
4. Choose a unique client name
5. Select who to talk to in the client UI
6. Receive a mixed return feed of the participants you have not muted locally

## Repository Layout

```text
.
|-- CMakeLists.txt            # Top-level build configuration
|-- client/                   # Qt Widgets desktop application
|-- server/                   # Control/audio mixing server
|-- shared/                   # Shared Opus and socket helpers
|-- audio_native/             # Optional native mixer / echo cancellation DLL
|-- third_party/opus/         # Vendored Opus headers + Windows binaries
|-- build/                    # Local build output (ignored)
`-- build-mingw/              # Alternate local build output (ignored)
```

## Features

- LAN server discovery on UDP port `50000`
- Client registration and control channel over TCP port `50001`
- Voice transport and mixed audio return over UDP port `50002`
- Opus encoding/decoding at `16 kHz`, mono, 20 ms frames
- Participant targeting: talk only to selected participants
- Local receive muting per participant
- Broadcast mode to target everyone in the room
- Automatic client heartbeat and stale client pruning
- Audio device selection for input and output
- Input gain, mic sensitivity, output/master volume controls
- Optional noise gate style suppression
- Optional automatic gain control
- Optional native echo cancellation when `native_mixer.dll` is available

## Requirements

### Software

- Windows 10 or Windows 11
- CMake `3.20+`
- A C++17 compiler
  - Visual Studio 2022 is the safest choice for this repository
  - MinGW can build the main app, but the native audio module is explicitly disabled for MinGW by the top-level CMake logic
- Qt 6 with `Widgets` and `UiTools`
- PortAudio runtime DLL

### Expected Local Paths

The current CMake defaults assume these paths unless you override them:

- `QT6_ROOT = C:/Qt/6.10.2`
- `PORTAUDIO_DLL = C:/msys64/ucrt64/bin/libportaudio.dll`

Opus is expected inside the repository:

- `third_party/opus/opus.lib`
- `third_party/opus/opus.dll`

If those files are missing, CMake will stop with a fatal error.

### Build Options

Top-level CMake options:

- `VOICE_BUILD_CLIENT=ON` builds the Qt desktop client
- `VOICE_BUILD_SERVER=ON` builds the voice server
- `VOICE_BUILD_AUDIO_NATIVE=OFF` builds the optional `native_mixer` DLL

By default:

- client: enabled
- server: enabled
- native mixer: disabled

## Build Instructions

### Visual Studio / MSVC

Configure:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
```

Build:

```powershell
cmake --build build --config Release
```

Artifacts are placed under:

- `build/bin/voice_server.exe`
- `build/bin/voice_client.exe`

The build also copies runtime dependencies such as:

- `opus.dll`
- `libportaudio.dll`
- client UI files in `client/ui`
- `technical-support.ico`

If Qt deployment tooling is found, `windeployqt.exe` is invoked automatically for the client target.

### Build With Native Echo Cancellation

This is optional and Windows/MSVC-oriented.

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 -DVOICE_BUILD_AUDIO_NATIVE=ON
cmake --build build --config Release
```

Notes:

- The repository vendors an AEC3 source tree under `audio_native/third_party/AEC3`
- If `TWOWAY_AEC3_DIR` is set, CMake will use that path instead
- MinGW builds automatically disable `VOICE_BUILD_AUDIO_NATIVE`

### MinGW

The repository already contains `build-mingw/`, which suggests MinGW has been used locally. The main client/server may build, but the bundled AEC3/native mixer path is intentionally disabled for MinGW because of known build issues.

Example configure:

```powershell
cmake -S . -B build-mingw -G "MinGW Makefiles"
cmake --build build-mingw
```

You may need to override `Qt6_DIR`, `QT6_ROOT`, and `PORTAUDIO_DLL` for your local setup.

## Running

### 1. Start the Server

From the build output directory:

```powershell
.\voice_server.exe
```

On startup the server binds:

- UDP discovery/audio receive: `50000` and `50002`
- TCP control: `50001`

Expected console output includes:

- audio UDP listening on `50002`
- control TCP listening on `50001`

### 2. Start the Client

```powershell
.\voice_client.exe
```

Client startup sequence:

1. Initialize WinSock and PortAudio
2. Open a local UDP receive socket on an ephemeral port
3. Discover the server by listening for `VOICE_SERVER` broadcasts on port `50000`
4. If discovery fails, prompt for manual server IP
5. Prompt for a unique client name
6. Register with the server and join the default room `main`
7. Open the main voice UI

### 3. Use the UI

Main behaviors visible in the code:

- `Mute Mic` toggles local transmit mute
- `Broadcast` selects all other visible participants as talk targets
- Each participant row has independent talk and mute controls
- The participant list auto-refreshes every 3 seconds
- A heartbeat pings the server every 8 seconds
- The settings dialog allows:
  - input device selection
  - output device selection
  - advanced audio controls
  - reconnect to server

## Configuration

### Registration Secret

Server and client both use the environment variable `VOICE_REGISTER_SECRET`.

If the variable is not set, both sides fall back to:

```text
mysecret
```

Set it before launching the server and clients if you want a non-default shared secret:

```powershell
$env:VOICE_REGISTER_SECRET="replace-this"
.\voice_server.exe
```

In a second terminal:

```powershell
$env:VOICE_REGISTER_SECRET="replace-this"
.\voice_client.exe
```

### Audio Device Runtime Dependency

The client loads PortAudio dynamically. It searches in this order:

1. `libportaudio.dll` beside the executable
2. `libportaudio.dll` via normal DLL search
3. the compiled fallback path from `VOICE_PORTAUDIO_DLL_FALLBACK`

If PortAudio cannot be loaded, the client throws an error during startup.

## Network Protocol Summary

This section documents the protocol implemented in the code today.

### Discovery

- Port: UDP `50000`
- Server broadcast payload: `VOICE_SERVER`
- Client probe payload: `VOICE_DISCOVER`

The client primarily waits for server broadcast announcements, and if needed also sends probes to:

- broadcast address
- `192.168.1.1`
- `192.168.0.1`
- `10.0.0.1`
- `192.168.1.255`
- `192.168.0.255`

### Control Commands

Transport: TCP `50001`

Commands currently implemented:

- `REGISTER:<client_id>:<audio_port>:<secret>`
- `LIST`
- `LIST:<client_id>`
- `PING:<client_id>`
- `JOIN:<client_id>:<room>`
- `TARGETS:<client_id>:<comma_separated_targets>`
- `TALK:<client_id>:<comma_separated_targets>`
- `HEAR:<client_id>:<comma_separated_targets>`
- `UNREGISTER:<client_id>`

Notable behavior:

- The default room is `main`
- Duplicate client IDs return `TAKEN`
- Invalid or unexpected messages return `ERR`
- Client IDs cannot contain `:`, `|`, `,`, or control characters

### Audio Transport

Transport: UDP `50002`

Client to server packet format:

```text
<client_id>|<seq>|<timestamp>:<opus_payload>
```

Server to client mixed packet format:

```text
MIXED|<seq>|<opus_payload>
```

The server:

- decodes incoming Opus frames
- tracks room membership
- keeps target/hear filters per client
- mixes active non-self sources for each listener
- re-encodes the mixed PCM with a listener-specific Opus encoder

## Architecture Notes

### Server

`voice_server` runs several background threads:

- discovery broadcast loop
- TCP control accept loop
- UDP audio receive loop
- mixed audio send loop
- stale client prune loop

Each room gets a `RoomMixer` instance that:

- stores the latest PCM frame per active sender
- drops stale sources quickly
- queues 20 ms mix frames
- creates per-listener Opus encoders on demand

### Client

`voice_client` combines:

- a Qt Widgets UI
- PortAudio capture and playback
- a UDP receive loop for incoming mixed audio
- a send thread that captures, processes, encodes, and transmits frames
- a heartbeat thread inside the main window

The client only starts transmit capture when at least one talk target is selected. If no targets remain, capture is stopped after a short delay.

### Audio Processing Notes

Current signal path in `AudioEngine`:

1. capture 16-bit mono PCM with PortAudio
2. calculate input peak / activity
3. optionally apply native echo cancellation
4. optionally apply a simple suppression gate
5. apply manual gain and optional auto gain
6. encode with Opus
7. send via UDP to the server

Receive path:

1. receive mixed UDP packet
2. extract Opus payload
3. decode to PCM
4. queue for playback
5. apply output/master volume
6. optionally feed reverse stream into the echo canceller

## Limitations and Known Constraints

- Windows-first codebase; not portable as-is
- Room selection is effectively fixed to the default room `main` in the client flow
- Discovery is LAN-oriented and depends on broadcast/local addressing assumptions
- There is no persistence, authentication system, or encryption beyond the shared registration secret
- The secret defaults to `mysecret`, which is not suitable for real deployments
- The client assumes the required DLLs are present at runtime
- No automated tests are present in this repository
- Large vendored native code under `audio_native/third_party/AEC3` makes builds heavier and repo size larger

## Troubleshooting

### Client cannot find the server

- Make sure `voice_server.exe` is running first
- Ensure Windows Firewall allows UDP `50000` and `50002`, and TCP `50001`
- Try manual IP entry when the startup dialog appears
- Verify client and server are on the same LAN/subnet

### Registration fails

- Check that the same `VOICE_REGISTER_SECRET` is set on both client and server
- Make sure the chosen client name is unique
- Avoid invalid characters in the client name: `:`, `|`, `,`, newline, tab

### No audio capture or playback

- Confirm `libportaudio.dll` is present beside `voice_client.exe` or reachable through the configured fallback path
- Open Settings and verify the input/output device selections
- Select at least one talk target; the client does not transmit until a target is chosen
- Check that `opus.dll` is present in the output directory

### Echo cancellation not available

- Build with `-DVOICE_BUILD_AUDIO_NATIVE=ON`
- Confirm `native_mixer.dll` is present in the runtime directory
- Use MSVC rather than MinGW for the native mixer path

## Development Notes

- Top-level output directories are configured to:
  - executables and DLLs: `build/bin`
  - static libraries: `build/lib`
- Qt UI files are loaded from the copied `ui/` directory at runtime
- The build copies `technical-support.ico` into the client output directory
- `.gitignore` excludes local build folders and Visual Studio user files

## License

This repository includes a top-level [LICENSE](LICENSE) file. Review third-party licensing as well for:

- Opus
- PortAudio
- vendored AEC3 / related upstream code
