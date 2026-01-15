import socket, threading

DISCOVERY_PORT = 50000
CONTROL_PORT = 50001
AUDIO_PORT = 50002

clients = {}      # id -> (ip, audio_port)
talking = {}      # id -> [target_ids]

def get_broadcast_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    local_ip = s.getsockname()[0]
    s.close()
    ip_parts = local_ip.split('.')
    ip_parts[3] = '255'
    return '.'.join(ip_parts)

def broadcast_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    broadcast_ip = get_broadcast_ip()
    print(f"Broadcasting to {broadcast_ip}:{DISCOVERY_PORT}")
    while True:
        s.sendto(b"VOICE_SERVER", (broadcast_ip, DISCOVERY_PORT))
        import time; time.sleep(2)


def control_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", CONTROL_PORT))

    while True:
        msg, addr = s.recvfrom(1024)
        ip = addr[0]
        text = msg.decode()

        # Client asks to register ID
        if text.startswith("REGISTER:"):
            cid, port = text.split(":")[1:]
            if cid in clients:
                s.sendto(b"TAKEN", addr)
            else:
                clients[cid] = (ip, int(port))
                s.sendto(b"OK", addr)
                print(f"{cid} joined from {ip}")

        # Client says who it wants to talk to
        elif text.startswith("TARGETS:"):
            cid, targets = text.split(":")[1:]
            talking[cid] = targets.split(",")

            # notify receivers
            for t in talking[cid]:
                if t in clients:
                    s.sendto(f"SPEAKING:{cid}".encode(), (clients[t][0], CONTROL_PORT))


def audio_router():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", AUDIO_PORT))

    while True:
        data, addr = s.recvfrom(4096)

        sender = None
        for cid, (ip, _) in clients.items():
            if ip == addr[0]:
                sender = cid

        if not sender or sender not in talking:
            continue

        for target in talking[sender]:
            if target in clients:
                ip, port = clients[target]
                s.sendto(data, (ip, port))


threading.Thread(target=broadcast_server, daemon=True).start()
threading.Thread(target=control_listener, daemon=True).start()
audio_router()
