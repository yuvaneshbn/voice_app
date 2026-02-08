import socket, threading, time

DISCOVERY_PORT = 50000
CONTROL_PORT   = 50001
AUDIO_PORT     = 50002

clients = {}   # client_id -> {ip, port, alive, registered_at}
talking = {}   # client_id -> set(target_ids)
last_seen = {}  # client_id -> last activity timestamp


def broadcast_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        s.sendto(b"VOICE_SERVER", ("<broadcast>", DISCOVERY_PORT))
        time.sleep(2)


def cleanup_inactive():
    while True:
        now = time.time()
        stale = [cid for cid, ts in last_seen.items() if now - ts > 5]
        for cid in stale:
            clients.pop(cid, None)
            talking.pop(cid, None)
            last_seen.pop(cid, None)
            print(f"[SERVER] {cid} timed out")
        time.sleep(1)


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

        # ---------- REGISTER ----------
        if text.startswith("REGISTER:"):
            cid, port = text.split(":")[1:]
            port = int(port)

            if cid in clients:
                s.sendto(b"TAKEN", addr)
                print(f"[SERVER] Client {cid} already in use")
                continue

            clients[cid] = {"ip": addr[0], "port": port, "alive": True, "registered_at": time.time()}
            talking[cid] = set()
            last_seen[cid] = time.time()
            s.sendto(b"OK", addr)
            print(f"[SERVER] {cid} registered from {addr[0]} on port {port}")
            print(f"[SERVER] Registered clients: {clients}")

        # ---------- TALK ----------
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
            clients[cid]["alive"] = True
            last_seen[cid] = time.time()

            print(f"[SERVER] {cid} -> {targets if targets else '(none)'}")
            print(f"[SERVER] Talking map: {dict((k, list(v)) for k, v in talking.items())}")

        # ---------- PING ----------
        elif text.startswith("PING:"):
            cid = text.split(":")[1]
            if cid in clients:
                clients[cid]["alive"] = True
                last_seen[cid] = time.time()

        # ---------- UNREGISTER ----------
        elif text.startswith("UNREGISTER:"):
            cid = text.split(":")[1]
            clients.pop(cid, None)
            talking.pop(cid, None)
            last_seen.pop(cid, None)
            print(f"[SERVER] {cid} disconnected")


def audio_router():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", AUDIO_PORT))

    print("[SERVER] Audio router running")
    packet_count = {}

    while True:
        if len(clients) < 2:
            time.sleep(0.05)
            continue
        try:
            packet, addr = s.recvfrom(4096)
        except ConnectionResetError:
            # UDP can raise this on Windows when remote port is unreachable
            continue

        # Audio packets are formatted as: client_id:opus_data
        if b":" not in packet:
            print(f"[SERVER] Malformed packet from {addr}")
            continue

        try:
            header_bytes, opus_data = packet.split(b":", 1)
            header = header_bytes.decode(errors="ignore").strip()
            if "|" not in header:
                continue
            # Header is client_id|seq|timestamp; keep only client_id for routing
            sender = header.split("|", 1)[0]
        except Exception as e:
            print(f"[SERVER] Parse error: {e}")
            continue

        # Track packet counts per sender
        packet_count[sender] = packet_count.get(sender, 0) + 1

        # Check if sender is registered and verify IP
        if sender not in clients:
            if packet_count[sender] % 500 == 1:
                print(f"[SERVER] Audio from unregistered sender: {sender} (registered: {list(clients.keys())})")
            continue

        if not clients[sender].get("alive", False):
            continue

        expected_ip, expected_port = clients[sender]["ip"], clients[sender]["port"]
        if addr[0] != expected_ip:
            if packet_count[sender] % 100 == 1:
                print(f"[SERVER] IP mismatch for {sender}: expected {expected_ip}, got {addr[0]}")
            continue

        last_seen[sender] = time.time()

        if sender not in talking:
            print(f"[SERVER] {sender} not in talking map (clients: {list(clients.keys())}, talking: {list(talking.keys())})")
            continue

        targets = talking[sender]

        if packet_count[sender] % 500 == 1:
            print(f"[SERVER] Audio #{packet_count[sender]} from {sender} -> targets: {targets if targets else '(no targets)'}")

        if not targets:
            continue

        for target in targets:
            if target == sender:
                continue
            if target in clients and clients[target].get("alive", False):
                ip, port = clients[target]["ip"], clients[target]["port"]
                try:
                    s.sendto(packet, (ip, port))
                except Exception as e:
                    print(f"[SERVER] Send error to {target}: {e}")
            else:
                print(f"[SERVER] Target {target} not available/alive")


threading.Thread(target=broadcast_server, daemon=True).start()
threading.Thread(target=control_listener, daemon=True).start()
threading.Thread(target=cleanup_inactive, daemon=True).start()
audio_router()
