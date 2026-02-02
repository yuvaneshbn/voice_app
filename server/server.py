import socket, threading, time

DISCOVERY_PORT = 50000
CONTROL_PORT   = 50001
AUDIO_PORT     = 50002

clients = {}   # client_id -> (ip, audio_port)
talking = {}   # client_id -> set(target_ids)


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

    print("🟢 Control listener running")

    while True:
        data, addr = s.recvfrom(1024)
        try:
            text = data.decode()
        except:
            continue

        # ---------- REGISTER ----------
        if text.startswith("REGISTER:"):
            cid, port = text.split(":")[1:]
            port = int(port)

            if cid in clients:
                s.sendto(b"TAKEN", addr)
                print(f"❌ Client {cid} already in use")
                continue

            clients[cid] = (addr[0], port)
            talking[cid] = set()
            s.sendto(b"OK", addr)
            print(f"✅ {cid} registered from {addr[0]} on port {port}")
            print(f"   Registered clients: {clients}")

        # ---------- TALK ----------
        elif text.startswith("TALK:"):
            parts = text.split(":")
            if len(parts) < 3:
                print(f"❌ Invalid TALK format: {text}")
                continue
            
            cid = parts[1]
            targets_str = parts[2]

            if cid not in clients:
                print(f"❌ TALK from unknown client: {cid} (registered clients: {list(clients.keys())})")
                continue

            targets = set(targets_str.split(",")) if targets_str.strip() else set()
            targets.discard("")  # Remove empty strings
            
            # Clean up old targets for this client
            old_targets = talking.get(cid, set())
            
            # Update this client's targets
            talking[cid] = targets

            print(f"[SERVER] 🎙️ {cid} → {targets if targets else '(none)'}")

            # IMPORTANT: NO bidirectional routing to avoid corruption
            # Each client only talks to explicitly selected targets

            print(f"   After bidirectional routing - Talking dict: {dict((k, list(v)) for k, v in talking.items())}")


        # ---------- UNREGISTER ----------
        elif text.startswith("UNREGISTER:"):
            cid = text.split(":")[1]
            clients.pop(cid, None)
            talking.pop(cid, None)
            print(f"🛑 {cid} disconnected")


def audio_router():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", AUDIO_PORT))

    print("🔊 Audio router running")
    packet_count = {cid: 0 for cid in ['1', '2', '3', '4']}  # Per-client counters

    while True:
        packet, addr = s.recvfrom(4096)
        
        # Audio packets are formatted as: client_id:opus_data
        if b":" not in packet:
            print(f"❌ Malformed packet from {addr}")
            continue
            
        try:
            sender_id_bytes, opus_data = packet.split(b":", 1)
            sender = sender_id_bytes.decode(errors="ignore").strip()
        except Exception as e:
            print(f"❌ Parse error: {e}")
            continue

        # Track packet counts per sender
        if sender in packet_count:
            packet_count[sender] += 1
        else:
            packet_count[sender] = 1

        # Check if sender is registered and verify IP
        if sender not in clients:
            if packet_count[sender] % 500 == 1:
                print(f"❌ Audio from unregistered sender: {sender} (registered: {list(clients.keys())})")
            continue
        
        # Verify sender IP matches registered IP
        expected_ip, expected_port = clients[sender]
        if addr[0] != expected_ip:
            if packet_count[sender] % 100 == 1:
                print(f"❌ IP mismatch for {sender}: expected {expected_ip}, got {addr[0]}")
            continue
        
        if sender not in talking:
            print(f"❌ {sender} not in talking dict (clients: {list(clients.keys())}, talking: {list(talking.keys())})")
            continue

        targets = talking[sender]
        
        # Log every N packets for each active sender
        if packet_count[sender] % 500 == 1:
            print(f"📡 Audio #{packet_count[sender]} from {sender} → targets: {targets if targets else '(no targets - not sending)'}")
        
        if not targets:
            continue
            
        # Forward to all targets
        for target in targets:
            if target in clients:
                ip, port = clients[target]
                try:
                    s.sendto(packet, (ip, port))
                except Exception as e:
                    print(f"❌ Send error to {target}: {e}")
            else:
                print(f"❌ Target {target} not in registered clients {list(clients.keys())}")


threading.Thread(target=broadcast_server, daemon=True).start()
threading.Thread(target=control_listener, daemon=True).start()
audio_router()
