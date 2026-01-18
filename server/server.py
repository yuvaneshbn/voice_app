import socket, threading

DISCOVERY_PORT = 50000
CONTROL_PORT = 50001
AUDIO_PORT = 50002

clients = {}   # client_id -> (ip, audio_port)
talking = {}   # client_id -> set(target_ids)

def broadcast_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        s.sendto(b"VOICE_SERVER", ("<broadcast>", DISCOVERY_PORT))
        import time; time.sleep(2)

def control_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", CONTROL_PORT))

    while True:
        msg, addr = s.recvfrom(1024)
        ip = addr[0]
        text = msg.decode()

        if text.startswith("REGISTER:"):
            cid, port = text.split(":")[1:]
            if cid in clients:
                s.sendto(b"TAKEN", addr)
            else:
                clients[cid] = (ip, int(port))
                s.sendto(b"OK", addr)
                print(f"{cid} registered from {ip}")

        elif text.startswith("TALK:"):
            cid, targets = text.split(":")[1:]
            talking[cid] = set(targets.split(",")) if targets else set()

def audio_router():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", AUDIO_PORT))

    while True:
        data, addr = s.recvfrom(4096)
        sender_ip = addr[0]

        sender = None
        for cid, (ip, _) in clients.items():
            if ip == sender_ip:
                sender = cid
                break

        if not sender or sender not in talking:
            continue

        for target in talking[sender]:
            if target in clients:
                ip, port = clients[target]
                s.sendto(sender.encode() + b":" + data, (ip, port))

threading.Thread(target=broadcast_server, daemon=True).start()
threading.Thread(target=control_listener, daemon=True).start()
audio_router()
