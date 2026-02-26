import asyncio
import hashlib
import logging
import socket
import threading
import time
from collections import defaultdict

DISCOVERY_PORT = 50000
CONTROL_PORT = 50001
AUDIO_PORT = 50002
DEFAULT_ROOM = "main"
MULTICAST_BASE = "239.0.0."
MULTICAST_TTL = 1
CLIENT_TIMEOUT_SEC = 30
SERVER_SECRET = "mysecret"


class Client:
    def __init__(self, client_id, ip, audio_port):
        self.client_id = client_id
        self.addr = (ip, audio_port)
        self.room = None
        self.targets = set()
        self.last_heartbeat = time.time()


class VoiceServer:
    def __init__(self):
        self.clients = {}
        self.rooms = defaultdict(set)
        self.packet_count = defaultdict(int)
        self.malformed_count = 0
        self.loop = None
        self.multicast_socks = {}

    @staticmethod
    def get_multicast_addr(room_id):
        room = room_id or DEFAULT_ROOM
        hash_val = int(hashlib.md5(room.encode("utf-8")).hexdigest(), 16) % 255 + 1
        return f"{MULTICAST_BASE}{hash_val}"

    @staticmethod
    def _validate_register(parts):
        # Backward-compatible: allow old REGISTER format if secret is not supplied.
        if len(parts) == 3:
            return True
        if len(parts) == 4:
            return parts[3] == SERVER_SECRET
        return False

    def broadcast_server(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while True:
            try:
                s.sendto(b"VOICE_SERVER", ("<broadcast>", DISCOVERY_PORT))
            except OSError as e:
                logging.error("Discovery broadcast error: %s", e)
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

            if cmd == "REGISTER" and self._validate_register(parts):
                audio_port = int(parts[2])
                if client_id in self.clients:
                    response = b"TAKEN\n"
                    logging.warning("Client %s already in use", client_id)
                else:
                    self.clients[client_id] = Client(client_id, peer_ip, audio_port)
                    self.join_room(client_id, DEFAULT_ROOM)
                    response = b"OK\n"
                    logging.info("%s registered from %s:%s", client_id, peer_ip, audio_port)

            elif cmd == "LIST":
                response = (",".join(sorted(self.clients.keys())) + "\n").encode()

            elif cmd == "PING" and client_id in self.clients:
                self.clients[client_id].last_heartbeat = time.time()
                response = b"OK\n"

            elif cmd == "JOIN" and len(parts) == 3 and client_id in self.clients:
                room_id = parts[2].strip() or DEFAULT_ROOM
                self.join_room(client_id, room_id)
                m_addr = self.get_multicast_addr(room_id)
                response = f"OK:{m_addr}\n".encode()

            elif cmd in ("TARGETS", "TALK") and client_id in self.clients:
                targets_str = parts[2] if len(parts) > 2 else ""
                targets = {t for t in targets_str.split(",") if t}
                self.clients[client_id].targets = targets
                response = b"OK\n"
                logging.info("%s targets updated: %s", client_id, sorted(targets))

            elif cmd == "UNREGISTER" and client_id in self.clients:
                self.remove_client(client_id)
                response = b"OK\n"

        except Exception as e:
            logging.exception("Control error from %s: %s", peer_ip, e)

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
        logging.info("%s joined room %s", client_id, room_id)

    def remove_client(self, client_id):
        client = self.clients.pop(client_id, None)
        if client and client.room:
            self.rooms[client.room].discard(client_id)
        logging.info("%s disconnected", client_id)

    async def prune_dead_clients(self):
        while True:
            now = time.time()
            stale = [
                cid
                for cid, cl in list(self.clients.items())
                if (now - cl.last_heartbeat) > CLIENT_TIMEOUT_SEC
            ]
            for cid in stale:
                self.remove_client(cid)
            await asyncio.sleep(10)

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

        logging.info("Audio UDP listening on port %s", AUDIO_PORT)

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
                logging.warning(
                    "Malformed audio packets=%s latest_from=%s",
                    self.malformed_count,
                    addr,
                )
            return

        sender = self.clients.get(sender_id)
        self.packet_count[sender_id] += 1
        pkt_count = self.packet_count[sender_id]

        if sender is None:
            if pkt_count % 500 == 1:
                logging.warning("Audio from unregistered sender: %s", sender_id)
            return

        if addr[0] != "unknown" and addr[0] != sender.addr[0] and pkt_count % 100 == 1:
            logging.warning(
                "IP mismatch warning for %s: expected %s, got %s. Allowing anyway.",
                sender_id,
                sender.addr[0],
                addr[0],
            )

        if sender.targets:
            target_ids = list(sender.targets)
            for target_id in target_ids:
                if target_id == sender_id:
                    continue
                target = self.clients.get(target_id)
                if target is None:
                    continue
                try:
                    await self.loop.sock_sendto(sock, packet, target.addr)
                except OSError as e:
                    logging.error("Send error to %s: %s", target_id, e)
        elif sender.room:
            m_addr = self.get_multicast_addr(sender.room)
            msock = self.multicast_socks.get(sender.room)
            if msock is None:
                msock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                msock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, MULTICAST_TTL)
                msock.setblocking(False)
                self.multicast_socks[sender.room] = msock
            try:
                await self.loop.sock_sendto(msock, packet, (m_addr, AUDIO_PORT))
            except OSError as e:
                logging.error("Multicast send error room=%s addr=%s: %s", sender.room, m_addr, e)
        else:
            return

    async def start(self):
        logging.basicConfig(
            level=logging.INFO,
            format="[SERVER] %(asctime)s %(levelname)s %(message)s",
        )
        self.loop = asyncio.get_running_loop()
        threading.Thread(target=self.broadcast_server, daemon=True, name="discovery-broadcast").start()

        control_server = await asyncio.start_server(self.handle_control, "0.0.0.0", CONTROL_PORT)
        logging.info("Control TCP listening on port %s", CONTROL_PORT)

        asyncio.create_task(self.prune_dead_clients())
        asyncio.create_task(self.start_audio_server())
        async with control_server:
            await control_server.serve_forever()


if __name__ == "__main__":
    asyncio.run(VoiceServer().start())
