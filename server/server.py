import asyncio
import hashlib
import logging
import os
import queue
import socket
import subprocess
import threading
import time
from collections import defaultdict

from opus_codec import OpusCodec
from room_mixer import PersonalizedMixer

DISCOVERY_PORT = 50000
CONTROL_PORT = 50001
AUDIO_PORT = 50002
DEFAULT_ROOM = "main"
MULTICAST_BASE = "239.0.0."
CLIENT_TIMEOUT_SEC = 30
SERVER_SECRET = "mysecret"
DSCP_EF = 46
DSCP_CS3 = 24
IP_TOS_EF = DSCP_EF << 2
IP_TOS_CS3 = DSCP_CS3 << 2
DECODE_WORKERS = 2
DECODE_QUEUE_MAX = 8192


def _set_socket_dscp(sock, ip_tos):
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, ip_tos)
    except OSError:
        pass


def _find_port_owners_windows(port, protocol):
    owners = []
    try:
        result = subprocess.run(
            ["netstat", "-aon", "-p", protocol],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return owners

    target = f":{port}"
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        if parts[0].upper() != protocol.upper():
            continue
        local_addr = parts[1]
        if not local_addr.endswith(target):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        owners.append(pid)
    return sorted(set(owners))


def _get_process_cmdline_windows(pid):
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f'(Get-CimInstance Win32_Process -Filter "ProcessId = {pid}").CommandLine',
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return ""
    return (result.stdout or "").strip()


def _kill_process_windows(pid):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        pass


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
        self.personal_mixers = {}

        self.packet_count = defaultdict(int)
        self.malformed_count = 0
        self.loop = None
        self.udp_sock = None

        self.state_lock = threading.RLock()
        self.decode_queue = queue.Queue(maxsize=DECODE_QUEUE_MAX)
        self.decode_workers = []
        self.running = False

    def _cleanup_stale_self_processes(self):
        if os.name != "nt":
            return

        current_pid = os.getpid()

        for protocol, port in (("TCP", CONTROL_PORT), ("UDP", AUDIO_PORT)):
            owners = _find_port_owners_windows(port, protocol)
            for pid in owners:
                if pid == current_pid:
                    continue
                cmdline = _get_process_cmdline_windows(pid).lower()
                if ("server.py" in cmdline) or cmdline.endswith("server.exe"):
                    logging.warning(
                        "Stopping stale server process pid=%s holding %s/%s",
                        pid,
                        protocol,
                        port,
                    )
                    _kill_process_windows(pid)

        # Give Windows a moment to release sockets after taskkill.
        time.sleep(0.3)

    def _raise_if_ports_busy(self):
        if os.name != "nt":
            return

        blockers = []
        current_pid = os.getpid()
        for protocol, port in (("TCP", CONTROL_PORT), ("UDP", AUDIO_PORT)):
            owners = [pid for pid in _find_port_owners_windows(port, protocol) if pid != current_pid]
            if not owners:
                continue
            details = []
            for pid in owners:
                cmdline = _get_process_cmdline_windows(pid)
                details.append(f"pid={pid} cmd={cmdline or 'unknown'}")
            blockers.append(f"{protocol}/{port}: " + "; ".join(details))

        if blockers:
            joined = " | ".join(blockers)
            raise RuntimeError(
                f"Required ports are busy ({joined}). "
                "Close the owning process and retry."
            )

    @staticmethod
    def get_multicast_addr(room_id):
        room = room_id or DEFAULT_ROOM
        hash_val = int(hashlib.md5(room.encode("utf-8")).hexdigest(), 16) % 255 + 1
        return f"{MULTICAST_BASE}{hash_val}"

    @staticmethod
    def _validate_register(parts):
        if len(parts) == 3:
            return True
        if len(parts) == 4:
            return parts[3] == SERVER_SECRET
        return False

    def broadcast_server(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        _set_socket_dscp(sock, IP_TOS_CS3)
        while True:
            try:
                sock.sendto(b"VOICE_SERVER", ("<broadcast>", DISCOVERY_PORT))
            except OSError as err:
                logging.error("Discovery broadcast error: %s", err)
            time.sleep(2)

    async def handle_control(self, reader, writer):
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else "0.0.0.0"
        response = b"ERR\n"

        ctrl_sock = writer.get_extra_info("socket")
        if ctrl_sock is not None:
            _set_socket_dscp(ctrl_sock, IP_TOS_CS3)
            try:
                ctrl_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

        try:
            raw = await reader.readline()
            message = raw.decode(errors="ignore").strip()
            parts = message.split(":")
            cmd = parts[0] if parts else ""
            client_id = parts[1] if len(parts) > 1 else ""

            if cmd == "REGISTER" and self._validate_register(parts):
                audio_port = int(parts[2])
                with self.state_lock:
                    taken = client_id in self.clients
                    if not taken:
                        self.clients[client_id] = Client(client_id, peer_ip, audio_port)
                if taken:
                    response = b"TAKEN\n"
                    logging.warning("Client %s already in use", client_id)
                else:
                    self.join_room(client_id, DEFAULT_ROOM)
                    response = b"OK\n"
                    logging.info("%s registered from %s:%s", client_id, peer_ip, audio_port)

            elif cmd == "LIST":
                with self.state_lock:
                    if client_id in self.clients:
                        room_id = self.clients[client_id].room
                        room_clients = self.rooms.get(room_id, set()) if room_id else set()
                        response = (",".join(sorted(room_clients)) + "\n").encode()
                    else:
                        response = (",".join(sorted(self.clients.keys())) + "\n").encode()

            elif cmd == "PING":
                with self.state_lock:
                    client = self.clients.get(client_id)
                    if client:
                        client.last_heartbeat = time.time()
                        response = b"OK\n"

            elif cmd == "JOIN" and len(parts) == 3:
                with self.state_lock:
                    exists = client_id in self.clients
                if exists:
                    room_id = parts[2].strip() or DEFAULT_ROOM
                    self.join_room(client_id, room_id)
                    m_addr = self.get_multicast_addr(room_id)
                    response = f"OK:{m_addr}\n".encode()

            elif cmd in ("TARGETS", "TALK"):
                with self.state_lock:
                    client = self.clients.get(client_id)
                    if client:
                        targets_str = parts[2] if len(parts) > 2 else ""
                        client.targets = {target for target in targets_str.split(",") if target}
                        response = b"OK\n"
                        logging.info("%s targets updated: %s", client_id, sorted(client.targets))

            elif cmd == "UNREGISTER":
                with self.state_lock:
                    exists = client_id in self.clients
                if exists:
                    self.remove_client(client_id)
                    response = b"OK\n"

        except Exception as err:
            logging.exception("Control error from %s: %s", peer_ip, err)

        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def join_room(self, client_id, room_id):
        with self.state_lock:
            client = self.clients.get(client_id)
            if client is None:
                return
            old_room = client.room
            if old_room and old_room != room_id:
                self.rooms[old_room].discard(client_id)
                if not self.rooms[old_room]:
                    self.rooms.pop(old_room, None)
            client.room = room_id
            self.rooms[room_id].add(client_id)
        logging.info("%s joined room %s", client_id, room_id)

    def remove_client(self, client_id):
        mixer_to_stop = None
        with self.state_lock:
            client = self.clients.pop(client_id, None)
            if client and client.room:
                self.rooms[client.room].discard(client_id)
                if not self.rooms[client.room]:
                    self.rooms.pop(client.room, None)

            mixer_to_stop = self.personal_mixers.pop(client_id, None)
            for mixer in self.personal_mixers.values():
                mixer.remove_source(client_id)

        if mixer_to_stop is not None:
            mixer_to_stop.stop()
        logging.info("%s disconnected", client_id)

    async def prune_dead_clients(self):
        while True:
            now = time.time()
            with self.state_lock:
                stale = [
                    cid
                    for cid, client in list(self.clients.items())
                    if (now - client.last_heartbeat) > CLIENT_TIMEOUT_SEC
                ]
            for client_id in stale:
                self.remove_client(client_id)
            await asyncio.sleep(10)

    @staticmethod
    def extract_sender_id(packet):
        try:
            parts = packet.split(b"|", 1)
            if len(parts) == 2:
                sender = parts[0].decode(errors="ignore").strip()
                if sender:
                    return sender

            if b":" in packet:
                sender = packet.split(b":", 1)[0].decode(errors="ignore").strip()
                if sender:
                    return sender
        except Exception:
            pass
        return None

    @staticmethod
    def extract_opus_payload(packet):
        if not packet:
            return b""
        if b":" in packet:
            return packet.split(b":", 1)[1]
        try:
            first_sep = packet.index(b"|")
            second_sep = packet.index(b"|", first_sep + 1)
            return packet[second_sep + 1 :]
        except ValueError:
            return b""

    def _start_decode_workers(self):
        if self.decode_workers:
            return
        self.running = True
        for idx in range(DECODE_WORKERS):
            worker = threading.Thread(
                target=self._decode_worker,
                args=(idx,),
                daemon=True,
                name=f"decode-{idx}",
            )
            worker.start()
            self.decode_workers.append(worker)

    def _decode_worker(self, worker_id):
        decoders = {}
        while self.running:
            try:
                sender_id, room_id, opus_payload = self.decode_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            decoder = decoders.get(sender_id)
            if decoder is None:
                try:
                    decoder = OpusCodec(
                        rate=16000,
                        channels=1,
                        frame_size=320,
                        create_encoder=False,
                        create_decoder=True,
                    )
                except Exception:
                    continue
                decoders[sender_id] = decoder

            try:
                pcm = decoder.decode(opus_payload)
            except Exception:
                continue

            if not pcm:
                continue
            self._dispatch_decoded_frame(sender_id, room_id, pcm)

    def _dispatch_decoded_frame(self, sender_id, room_id, pcm):
        mixers_to_start = []
        target_mixers = []
        with self.state_lock:
            sender = self.clients.get(sender_id)
            if sender is None or sender.room != room_id:
                return
            if not sender.targets:
                return

            room_clients = list(self.rooms.get(room_id, set()))
            for listener_id in room_clients:
                if listener_id == sender_id:
                    continue
                if listener_id not in sender.targets:
                    continue
                listener = self.clients.get(listener_id)
                if listener is None:
                    continue

                mixer = self.personal_mixers.get(listener_id)
                if mixer is None:
                    mixer = PersonalizedMixer(listener_id)
                    self.personal_mixers[listener_id] = mixer
                    mixers_to_start.append(mixer)
                target_mixers.append(mixer)

        for mixer in mixers_to_start:
            mixer.start()
        for mixer in target_mixers:
            mixer.add_pcm(sender_id, pcm)

    async def start_audio_server(self):
        logging.info("Audio UDP listening on port %s", AUDIO_PORT)
        while True:
            packet, addr = await self.loop.sock_recvfrom(self.udp_sock, 4096)
            self.forward_packet(packet, addr)

    def forward_packet(self, packet, addr):
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

        with self.state_lock:
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

        room_id = sender.room
        if not room_id:
            return

        opus_payload = self.extract_opus_payload(packet)
        if not opus_payload:
            return

        try:
            self.decode_queue.put_nowait((sender_id, room_id, opus_payload))
        except queue.Full:
            if pkt_count % 200 == 1:
                logging.warning("Decode queue full; dropping packet from %s", sender_id)

    async def send_personalized_mixes(self):
        while True:
            with self.state_lock:
                mixers = list(self.personal_mixers.items())
                listeners = {cid: self.clients.get(cid) for cid, _ in mixers}

            stale_listener_ids = []
            for listener_id, mixer in mixers:
                listener = listeners.get(listener_id)
                if listener is None:
                    stale_listener_ids.append(listener_id)
                    continue
                for packet in mixer.drain_packets(limit=64):
                    try:
                        await self.loop.sock_sendto(self.udp_sock, packet, listener.addr)
                    except OSError as err:
                        logging.error("Unicast send error to %s: %s", listener_id, err)

            for stale_id in stale_listener_ids:
                self.remove_client(stale_id)
            await asyncio.sleep(0.002)

    async def start(self):
        logging.basicConfig(
            level=logging.INFO,
            format="[SERVER] %(asctime)s %(levelname)s %(message)s",
        )
        self.loop = asyncio.get_running_loop()

        self._cleanup_stale_self_processes()
        self._raise_if_ports_busy()

        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        _set_socket_dscp(self.udp_sock, IP_TOS_EF)
        self.udp_sock.bind(("0.0.0.0", AUDIO_PORT))
        self.udp_sock.setblocking(False)

        self._start_decode_workers()
        threading.Thread(
            target=self.broadcast_server,
            daemon=True,
            name="discovery-broadcast",
        ).start()

        control_server = await asyncio.start_server(self.handle_control, "0.0.0.0", CONTROL_PORT)
        for sock in control_server.sockets or []:
            _set_socket_dscp(sock, IP_TOS_CS3)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        logging.info("Control TCP listening on port %s", CONTROL_PORT)

        asyncio.create_task(self.prune_dead_clients())
        asyncio.create_task(self.start_audio_server())
        asyncio.create_task(self.send_personalized_mixes())

        async with control_server:
            await control_server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(VoiceServer().start())
    except RuntimeError as err:
        print(f"[SERVER] Startup failed: {err}")
