# Voice App - LAN Intercom System

A UDP-based LAN intercom system that allows multiple PCs on the same network to communicate via voice. Supports private messaging, broadcasting, and selective hearing.

## Features

- **Client Identity**: Select unique IDs (Client 1-4).
- **Private Talk**: Send voice to specific clients.
- **Broadcast**: Send voice to all clients.
- **Selective Hearing**: Control which clients' audio you listen to.
- **Real-time Audio**: Low-latency voice communication over LAN.

## Requirements

- Python 3.7+
- Windows/Linux/Mac (with audio devices)
- All PCs on the same local network (same subnet)

## Installation

1. Clone or download the project.
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
   Or for offline installation:
   ```
   .\offline_install.bat
   ```
3. (Optional) Build executables with PyInstaller:
   - For client: `cd client && pyinstaller main.spec`
   - For server: `cd server && pyinstaller server.spec`
   - Run the EXEs from `dist/` folders.

## Usage

### Running the Server
On one PC:
```
cd server
python server.py
```
The server broadcasts its presence and handles audio routing.

### Running Clients
On each client PC:
```
cd client
python main.py
```
- Auto-discovers server; if not found, enter IP manually.
- Select a unique Client ID (1-4).
- Use TALK buttons (bottom row) to select recipients.
- Use HEAR buttons (upper row) to select who to listen to.
- Click TALK (center) for broadcast.

## Network Setup

- Ensure firewalls allow UDP on ports 50000-50002.
- Test with `ping` between PCs.
- For LAN, use same Wi-Fi or Ethernet.

## Troubleshooting

- **No Audio**: Check microphone/speaker settings.
- **Discovery Fails**: Enter server IP manually.
- **Port Issues**: Ensure ports are open.
- **Dependencies**: Run `pip install -r requirements.txt`.

## Project Structure

- `client/`: Client application files.

https://drive.google.com/drive/folders/1dsLQTv7MZin_437LcpNiiCrt1tE7szNK?usp=drive_link
- `server/`: Server application files.
- `requirements.txt`: Python dependencies.
- `README.md`: This file.

## License

None specified.
