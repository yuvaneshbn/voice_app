import asyncio
import socket
import threading
import time
from collections import defaultdict

DISCOVERY_PORT = 50000
CONTROL_PORT = 50001
AUDIO_PORT = 50002
DEFAULT_ROOM = "main"


class Client:
    def __init__(self, client_id, ip, audio_port):
        self.client_id = client_id
        self.addr = (ip, audio_port)
        self.room = None
        self.targets = set()


class VoiceServer:
    def __init__(self):
        self.clients = {}
        self.rooms = defaultdict(set)
        self.packet_count = defaultdict(int)
        self.malformed_count = 0
        self.loop = None

    def broadcast_server(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while True:
            try:
                s.sendto(b"VOICE_SERVER", ("<broadcast>", DISCOVERY_PORT))
            except OSError as e:
                print(f"[SERVER] Discovery broadcast error: {e}")
            time.sleep(2)

    async def handle_control(self, reader, writer):
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else "0.0.0.0"
        response = b"ERR\n"

        try:
            raw = await reader.readline()
            message = raw.decode(errors="ignore").strip()
            parts = message.split(":")
            cmd = parts[0] if parts else ""
            client_id = parts[1] if len(parts) > 1 else ""

            if cmd == "REGISTER" and len(parts) == 3:
                audio_port = int(parts[2])
                if client_id in self.clients:
                    response = b"TAKEN\n"
                    print(f"[SERVER] Client {client_id} already in use")
                else:
                    self.clients[client_id] = Client(client_id, peer_ip, audio_port)
                    self.join_room(client_id, DEFAULT_ROOM)
                    response = b"OK\n"
                    print(f"[SERVER] {client_id} registered from {peer_ip}:{audio_port}")

            elif cmd == "JOIN" and len(parts) == 3 and client_id in self.clients:
                room_id = parts[2].strip() or DEFAULT_ROOM
                self.join_room(client_id, room_id)
                response = b"OK\n"

            elif cmd in ("TARGETS", "TALK") and client_id in self.clients:
                targets_str = parts[2] if len(parts) > 2 else ""
                targets = {t for t in targets_str.split(",") if t}
                self.clients[client_id].targets = targets
                response = b"OK\n"
                print(f"[SERVER] {client_id} targets updated: {sorted(targets)}")

            elif cmd == "UNREGISTER" and client_id in self.clients:
                self.remove_client(client_id)
                response = b"OK\n"

        except Exception as e:
            print(f"[SERVER] Control error from {peer_ip}: {e}")

        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def join_room(self, client_id, room_id):
        client = self.clients.get(client_id)
        if client is None:
            return
        if client.room:
            self.rooms[client.room].discard(client_id)
        client.room = room_id
        self.rooms[room_id].add(client_id)
        print(f"[SERVER] {client_id} joined room {room_id}")

    def remove_client(self, client_id):
        client = self.clients.pop(client_id, None)
        if client and client.room:
            self.rooms[client.room].discard(client_id)
        print(f"[SERVER] {client_id} disconnected")

    @staticmethod
    def extract_sender_id(packet):
        parts = packet.split(b"|", 1)
        if len(parts) == 2:
            sender = parts[0].decode(errors="ignore").strip()
            if sender:
                return sender

        if b":" in packet:
            sender = packet.split(b":", 1)[0].decode(errors="ignore").strip()
            if sender:
                return sender
        return None

    async def start_audio_server(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        sock.bind(("0.0.0.0", AUDIO_PORT))
        sock.setblocking(False)

        print(f"[SERVER] Audio UDP listening on port {AUDIO_PORT}")

        while True:
            packet, addr = await self.loop.sock_recvfrom(sock, 4096)
            asyncio.create_task(self.forward_packet(sock, packet, addr))

    async def forward_packet(self, sock, packet, addr):
        if not packet:
            return
        if addr is None:
            addr = ("unknown", 0)

        sender_id = self.extract_sender_id(packet)
        if sender_id is None:
            self.malformed_count += 1
            if self.malformed_count % 50 == 1:
                print(f"[SERVER] Malformed audio packets={self.malformed_count} latest_from={addr}")
            return

        sender = self.clients.get(sender_id)
        self.packet_count[sender_id] += 1
        pkt_count = self.packet_count[sender_id]

        if sender is None:
            if pkt_count % 500 == 1:
                print(f"[SERVER] Audio from unregistered sender: {sender_id}")
            return

        if addr[0] != "unknown" and addr[0] != sender.addr[0] and pkt_count % 100 == 1:
            print(
                f"[SERVER] IP mismatch warning for {sender_id}: "
                f"expected {sender.addr[0]}, got {addr[0]}. Allowing anyway."
            )

        if sender.targets:
            target_ids = list(sender.targets)
        elif sender.room:
            target_ids = list(self.rooms[sender.room])
        else:
            return

        for target_id in target_ids:
            if target_id == sender_id:
                continue
            target = self.clients.get(target_id)
            if target is None:
                continue
            try:
                await self.loop.sock_sendto(sock, packet, target.addr)
            except OSError as e:
                print(f"[SERVER] Send error to {target_id}: {e}")

    async def start(self):
        self.loop = asyncio.get_running_loop()
        threading.Thread(target=self.broadcast_server, daemon=True, name="discovery-broadcast").start()

        control_server = await asyncio.start_server(self.handle_control, "0.0.0.0", CONTROL_PORT)
        print(f"[SERVER] Control TCP listening on port {CONTROL_PORT}")

        asyncio.create_task(self.start_audio_server())
        async with control_server:
            await control_server.serve_forever()


if __name__ == "__main__":
    asyncio.run(VoiceServer().start())
