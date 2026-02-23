import socket
import threading
import time

DISCOVERY_PORT = 50000
CONTROL_PORT = 50001
AUDIO_PORT = 50002
DROP_SILENT_VAD = False

clients = {}  # client_id -> (ip, audio_port)
talking = {}  # client_id -> set(target_ids)


def broadcast_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        s.sendto(b"VOICE_SERVER", ("<broadcast>", DISCOVERY_PORT))
        time.sleep(2)


def control_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", CONTROL_PORT))

    print("[SERVER] Control listener running")

    while True:
        data, addr = s.recvfrom(1024)
        try:
            text = data.decode()
        except Exception:
            continue

        if text.startswith("REGISTER:"):
            cid, port = text.split(":")[1:]
            port = int(port)

            if cid in clients:
                s.sendto(b"TAKEN", addr)
                print(f"[SERVER] Client {cid} already in use")
                continue

            clients[cid] = (addr[0], port)
            talking[cid] = set()
            s.sendto(b"OK", addr)
            print(f"[SERVER] {cid} registered from {addr[0]} on port {port}")
            print(f"[SERVER] Registered clients: {clients}")

        elif text.startswith("TALK:"):
            parts = text.split(":")
            if len(parts) < 3:
                print(f"[SERVER] Invalid TALK format: {text}")
                continue

            cid = parts[1]
            targets_str = parts[2]

            if cid not in clients:
                print(f"[SERVER] TALK from unknown client: {cid} (registered clients: {list(clients.keys())})")
                continue

            targets = set(targets_str.split(",")) if targets_str.strip() else set()
            targets.discard("")
            talking[cid] = targets

            print(f"[SERVER] {cid} -> {targets if targets else '(none)'}")
            print(f"[SERVER] Talking map: {dict((k, list(v)) for k, v in talking.items())}")

        elif text.startswith("UNREGISTER:"):
            cid = text.split(":")[1]
            clients.pop(cid, None)
            talking.pop(cid, None)
            print(f"[SERVER] {cid} disconnected")


def parse_audio_packet(packet):
    # New format: sender|seq|timestamp|vad|opus_payload
    parts = packet.split(b"|", 4)
    if len(parts) == 5:
        try:
            sender = parts[0].decode(errors="ignore").strip()
            seq = int(parts[1]) & 0xFFFF
            ts = int(parts[2])
            vad = int(parts[3])
            opus = parts[4]
            return sender, seq, ts, vad, opus, False
        except Exception:
            pass

    # Legacy fallback: sender:opus_payload
    if b":" in packet:
        try:
            sender_id_bytes, opus = packet.split(b":", 1)
            sender = sender_id_bytes.decode(errors="ignore").strip()
            return sender, None, None, 1, opus, True
        except Exception:
            return None, None, None, None, None, None

    return None, None, None, None, None, None


def audio_router():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", AUDIO_PORT))

    print("[SERVER] Audio router running")
    packet_count = {}

    while True:
        packet, addr = s.recvfrom(4096)

        sender, seq, ts, vad, opus, legacy = parse_audio_packet(packet)
        del seq, ts, opus, legacy

        if sender is None:
            print(f"[SERVER] Malformed packet from {addr}")
            continue

        packet_count[sender] = packet_count.get(sender, 0) + 1

        if sender not in clients:
            if packet_count[sender] % 500 == 1:
                print(f"[SERVER] Audio from unregistered sender: {sender} (registered: {list(clients.keys())})")
            continue

        expected_ip, _expected_port = clients[sender]
        if addr[0] != expected_ip:
            if packet_count[sender] % 100 == 1:
                print(f"[SERVER] IP mismatch for {sender}: expected {expected_ip}, got {addr[0]}")
            continue

        if sender not in talking:
            print(f"[SERVER] {sender} not in talking map")
            continue

        if DROP_SILENT_VAD and vad == 0:
            if packet_count[sender] % 500 == 1:
                print(f"[SERVER] Silent frame from {sender} skipped")
            continue

        targets = talking[sender]

        if packet_count[sender] % 500 == 1:
            print(f"[SERVER] Audio #{packet_count[sender]} from {sender} -> {targets if targets else '(no targets)'}")

        if not targets:
            continue

        for target in targets:
            if target == sender:
                # Never loop sender audio back to itself.
                continue
            if target in clients:
                ip, port = clients[target]
                try:
                    s.sendto(packet, (ip, port))
                except Exception as e:
                    print(f"[SERVER] Send error to {target}: {e}")
            else:
                print(f"[SERVER] Target {target} not in registered clients {list(clients.keys())}")


threading.Thread(target=broadcast_server, daemon=True).start()
threading.Thread(target=control_listener, daemon=True).start()
audio_router()
