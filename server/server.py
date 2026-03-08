import asyncio
import hashlib
import logging
import queue
import socket
import struct
import threading
import time
from collections import defaultdict

from opus_codec import OpusCodec

DISCOVERY_PORT = 50000
CONTROL_PORT = 50001
AUDIO_PORT = 50002
DEFAULT_ROOM = "main"
MULTICAST_BASE = "239.0.0."
CLIENT_TIMEOUT_SEC = 35
SERVER_SECRET = "mysecret"
DSCP_EF = 46
DSCP_CS3 = 24
IP_TOS_EF = DSCP_EF << 2
IP_TOS_CS3 = DSCP_CS3 << 2
MIX_FRAME_SAMPLES = 320
MIX_FRAME_BYTES = MIX_FRAME_SAMPLES * 2
ROOM_SOURCE_STALE_SEC = 0.25
ROOM_MIX_QUEUE_MAX = 128
VAD_PEAK_THRESHOLD = 120


def _set_socket_dscp(sock, ip_tos):
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, ip_tos)
    except OSError:
        pass


class Client:
    def __init__(self, client_id, ip, audio_port):
        self.client_id = client_id
        self.addr = (ip, audio_port)
        self.room = None
        self.targets = set()
        self.hear_targets = None  # None means allow all speakers in room.
        self.last_heartbeat = time.time()
        self.cpu_score = 0.0
        self.latency_ms = 9999.0
        self.is_router = False


class RoomMixer:
    """One mixer thread per room with a bounded output frame queue."""

    def __init__(self, room_id):
        self.room_id = room_id
        self.sources = {}
        self.last_seen = {}
        self.lock = threading.Lock()
        self.seq = 0
        self.running = True
        self.listener_encoders = {}
        self.mixed_queue = queue.Queue(maxsize=ROOM_MIX_QUEUE_MAX)
        self._thread = threading.Thread(
            target=self._mix_loop,
            daemon=True,
            name=f"room-mix-{room_id}",
        )
        self._thread.start()

    def add_pcm(self, sender_id, pcm_bytes):
        if not pcm_bytes:
            return
        pcm = (pcm_bytes + (b"\x00" * MIX_FRAME_BYTES))[:MIX_FRAME_BYTES]
        if max(abs(sample) for sample in struct.unpack("<320h", pcm)) < VAD_PEAK_THRESHOLD:
            with self.lock:
                self.sources.pop(sender_id, None)
                self.last_seen.pop(sender_id, None)
            return
        now = time.time()
        with self.lock:
            self.sources[sender_id] = pcm
            self.last_seen[sender_id] = now

    def remove_source(self, sender_id):
        with self.lock:
            self.sources.pop(sender_id, None)
            self.last_seen.pop(sender_id, None)

    def _collect_frames_locked(self):
        now = time.time()
        stale = [
            sender_id
            for sender_id, last_seen in self.last_seen.items()
            if (now - last_seen) > ROOM_SOURCE_STALE_SEC
        ]
        for sender_id in stale:
            self.sources.pop(sender_id, None)
            self.last_seen.pop(sender_id, None)

        return list(self.sources.items())

    @staticmethod
    def _mix_pcm_frames(frames):
        mixed = [0] * MIX_FRAME_SAMPLES
        for frame in frames:
            pcm = (frame + (b"\x00" * MIX_FRAME_BYTES))[:MIX_FRAME_BYTES]
            samples = struct.unpack("<320h", pcm)
            for idx, sample in enumerate(samples):
                mixed[idx] += sample

        active = len(frames)
        if active <= 1:
            gain = 1.0
        elif active == 2:
            gain = 0.95
        else:
            gain = 2.5 / float(active)

        scaled = [int(sample * gain) for sample in mixed]
        clamped = [max(-32768, min(32767, sample)) for sample in scaled]
        return struct.pack("<320h", *clamped)

    def _enqueue_mix_frame(self, source_items):
        frame = (self.seq, tuple(source_items))
        self.seq = (self.seq + 1) & 0xFFFF
        try:
            self.mixed_queue.put_nowait(frame)
        except queue.Full:
            try:
                self.mixed_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.mixed_queue.put_nowait(frame)
            except queue.Full:
                pass

    def get_or_create_listener_encoder(self, listener_id):
        with self.lock:
            encoder = self.listener_encoders.get(listener_id)
            if encoder is None:
                encoder = OpusCodec(
                    rate=16000,
                    channels=1,
                    frame_size=MIX_FRAME_SAMPLES,
                    bitrate=48000,
                    create_encoder=True,
                    create_decoder=False,
                )
                self.listener_encoders[listener_id] = encoder
            return encoder

    def remove_listener(self, listener_id):
        with self.lock:
            self.listener_encoders.pop(listener_id, None)

    def _mix_loop(self):
        frame_period = 0.020
        while self.running:
            start = time.perf_counter()
            with self.lock:
                source_items = self._collect_frames_locked()

            if source_items:
                self._enqueue_mix_frame(source_items)

            elapsed = time.perf_counter() - start
            time.sleep(max(0.0, frame_period - elapsed))

    def drain_packets(self, limit=64):
        packets = []
        for _ in range(limit):
            try:
                packets.append(self.mixed_queue.get_nowait())
            except queue.Empty:
                break
        return packets

    def stop(self):
        self.running = False
        if self._thread.is_alive():
            self._thread.join(timeout=0.8)


class VoiceServer:
    def __init__(self):
        self.clients = {}
        self.rooms = defaultdict(set)
        self.room_mixers = {}
        self.sender_decoders = {}

        self.packet_count = defaultdict(int)
        self.malformed_count = 0
        self.loop = None
        self.udp_sock = None
        self.control_server = None
        self.tasks = []
        self.discovery_thread = None
        self.running = False
        self._shutdown_done = False

        self.state_lock = threading.RLock()

    @staticmethod
    def _router_count_for_room(member_count):
        if member_count <= 0:
            return 0
        if member_count <= 50:
            return 1
        if member_count <= 120:
            return 2
        return 3

    def _recompute_room_topology_locked(self, room_id):
        members = [self.clients[cid] for cid in self.rooms.get(room_id, set()) if cid in self.clients]
        for client in members:
            client.is_router = False

        if not members:
            return

        router_count = min(len(members), self._router_count_for_room(len(members)))
        ranked = sorted(
            members,
            key=lambda c: (-float(c.cpu_score), float(c.latency_ms), c.client_id),
        )
        for router in ranked[:router_count]:
            router.is_router = True

    def _route_snapshot_for_client_locked(self, client_id):
        client = self.clients.get(client_id)
        if client is None or not client.room:
            return {"role": "leaf", "up": None, "down": []}

        room_members = [self.clients[cid] for cid in self.rooms.get(client.room, set()) if cid in self.clients]
        routers = [member for member in room_members if member.is_router]
        routers.sort(key=lambda c: c.client_id)
        root_router = routers[0] if routers else None

        up = None
        down = []

        if client.is_router:
            role = "router"
            if root_router is not None and root_router.client_id != client.client_id:
                up = root_router.addr

            if root_router is not None and root_router.client_id == client.client_id:
                for router in routers[1:]:
                    down.append((router.client_id, router.addr))
                leaves = [m for m in room_members if not m.is_router]
                child_slots = [root_router] + routers[1:]
                for idx, leaf in enumerate(sorted(leaves, key=lambda c: c.client_id)):
                    assigned_router = child_slots[idx % len(child_slots)]
                    if assigned_router.client_id == client.client_id:
                        down.append((leaf.client_id, leaf.addr))
            else:
                for member in room_members:
                    if member.is_router or member.client_id == client.client_id:
                        continue
                    down.append((member.client_id, member.addr))
        else:
            role = "leaf"
            if root_router is not None:
                if len(routers) == 1:
                    assigned_router = root_router
                else:
                    leaves = sorted([m for m in room_members if not m.is_router], key=lambda c: c.client_id)
                    routers_cycle = [root_router] + routers[1:]
                    assignment = {}
                    for idx, leaf in enumerate(leaves):
                        assignment[leaf.client_id] = routers_cycle[idx % len(routers_cycle)]
                    assigned_router = assignment.get(client.client_id, root_router)
                up = assigned_router.addr

        return {"role": role, "up": up, "down": down}

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

    @staticmethod
    def _is_valid_client_id(client_id):
        cid = (client_id or "").strip()
        if not cid:
            return False
        if any(ch in cid for ch in (":", "|", "\n", "\r", "\t")):
            return False
        # Comma is reserved by legacy TARGETS csv parsing.
        if "," in cid:
            return False
        return True

    @staticmethod
    def _normalize_client_id(client_id):
        return (client_id or "").strip().rstrip(",")

    def _resolve_targets_locked(self, sender_id, targets_str):
        sender = self.clients.get(sender_id)
        if sender is None or not sender.room:
            return set()

        room_members = self.rooms.get(sender.room, set())
        normalized_members = defaultdict(list)
        for member in room_members:
            normalized_members[self._normalize_client_id(member)].append(member)

        resolved = set()
        raw_targets = [target.strip() for target in (targets_str or "").split(",") if target.strip()]
        for target in raw_targets:
            if target in room_members and target != sender_id:
                resolved.add(target)
                continue

            norm = self._normalize_client_id(target)
            matches = [member for member in normalized_members.get(norm, []) if member != sender_id]
            if len(matches) == 1:
                resolved.add(matches[0])
        return resolved

    def _collect_stale_client_ids_locked(self, now):
        return [
            cid
            for cid, client in self.clients.items()
            if (now - client.last_heartbeat) > CLIENT_TIMEOUT_SEC
        ]

    def _prune_stale_clients_now(self):
        with self.state_lock:
            stale = self._collect_stale_client_ids_locked(time.time())
        for client_id in stale:
            self.remove_client(client_id)

    def _touch_client_locked(self, client_id):
        client = self.clients.get(client_id)
        if client is not None:
            client.last_heartbeat = time.time()

    def get_or_create_mixer(self, room_id):
        with self.state_lock:
            mixer = self.room_mixers.get(room_id)
            if mixer is None:
                mixer = RoomMixer(room_id)
                self.room_mixers[room_id] = mixer
            return mixer

    def broadcast_server(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        _set_socket_dscp(sock, IP_TOS_CS3)
        try:
            while self.running:
                try:
                    sock.sendto(b"VOICE_SERVER", ("<broadcast>", DISCOVERY_PORT))
                except OSError as err:
                    logging.debug("Discovery broadcast error: %s", err)
                for _ in range(20):
                    if not self.running:
                        break
                    time.sleep(0.1)
        finally:
            try:
                sock.close()
            except OSError:
                pass

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

        self._prune_stale_clients_now()

        try:
            raw = await reader.readline()
            message = raw.decode(errors="ignore").strip()
            parts = message.split(":")
            cmd = parts[0] if parts else ""
            client_id = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "REGISTER" and self._validate_register(parts):
                if len(parts) < 3 or not client_id or not self._is_valid_client_id(client_id):
                    response = b"ERR\n"
                else:
                    try:
                        audio_port = int(parts[2])
                    except ValueError:
                        audio_port = 0

                    if audio_port <= 0:
                        response = b"ERR\n"
                    else:
                        with self.state_lock:
                            taken = client_id in self.clients
                            if not taken:
                                self.clients[client_id] = Client(client_id, peer_ip, audio_port)
                        if taken:
                            response = b"TAKEN\n"
                            logging.warning("Client %s already in use", client_id)
                        else:
                            response = b"OK\n"
                            logging.info("%s registered from %s:%s", client_id, peer_ip, audio_port)

            elif cmd == "LIST":
                with self.state_lock:
                    if client_id and client_id in self.clients:
                        room_id = self.clients[client_id].room
                        room_clients = self.rooms.get(room_id, set()) if room_id else set()
                        response = ("\n".join(sorted(room_clients)) + "\n").encode()
                    else:
                        response = ("\n".join(sorted(self.clients.keys())) + "\n").encode()

            elif cmd == "PING":
                with self.state_lock:
                    if client_id in self.clients:
                        self._touch_client_locked(client_id)
                        response = b"OK\n"

            elif cmd == "JOIN" and len(parts) == 3:
                with self.state_lock:
                    exists = client_id in self.clients
                if exists:
                    room_id = parts[2].strip() or DEFAULT_ROOM
                    self.join_room(client_id, room_id)
                    m_addr = self.get_multicast_addr(room_id)
                    response = f"OK:{m_addr}\n".encode()

            elif cmd == "METRICS" and len(parts) >= 4:
                with self.state_lock:
                    client = self.clients.get(client_id)
                    if client is not None:
                        try:
                            client.cpu_score = max(0.0, min(100.0, float(parts[2])))
                        except ValueError:
                            client.cpu_score = 0.0
                        try:
                            client.latency_ms = max(1.0, min(9999.0, float(parts[3])))
                        except ValueError:
                            client.latency_ms = 9999.0
                        self._touch_client_locked(client_id)
                        if client.room:
                            self._recompute_room_topology_locked(client.room)
                        response = b"OK\n"

            elif cmd == "ROUTE":
                with self.state_lock:
                    client = self.clients.get(client_id)
                    if client is not None and client.room:
                        self._recompute_room_topology_locked(client.room)
                        route = self._route_snapshot_for_client_locked(client_id)
                        up = route["up"]
                        up_s = f"{up[0]}:{up[1]}" if up else "-"
                        down_s = ",".join(f"{cid}@{addr[0]}:{addr[1]}" for cid, addr in route["down"])
                        response = f"OK:ROLE={route['role']};UP={up_s};DOWN={down_s}\n".encode()

            elif cmd in ("TARGETS", "TALK"):
                with self.state_lock:
                    client = self.clients.get(client_id)
                    if client:
                        targets_str = parts[2] if len(parts) > 2 else ""
                        client.targets = self._resolve_targets_locked(client_id, targets_str)
                        self._touch_client_locked(client_id)
                        response = b"OK\n"

            elif cmd == "HEAR":
                with self.state_lock:
                    client = self.clients.get(client_id)
                    if client:
                        hear_str = parts[2] if len(parts) > 2 else ""
                        client.hear_targets = self._resolve_targets_locked(client_id, hear_str)
                        self._touch_client_locked(client_id)
                        response = b"OK\n"

            elif cmd == "UNREGISTER":
                with self.state_lock:
                    exists = client_id in self.clients
                if exists:
                    self.remove_client(client_id)
                    response = b"OK\n"

        except Exception as err:
            logging.exception("Control error from %s: %s", peer_ip, err)

        try:
            writer.write(response)
            await writer.drain()
        except Exception:
            pass
        writer.close()
        await writer.wait_closed()

    def join_room(self, client_id, room_id):
        old_mixer = None
        with self.state_lock:
            client = self.clients.get(client_id)
            if client is None:
                return
            old_room = client.room
            if old_room == room_id and client_id in self.rooms.get(room_id, set()):
                client.last_heartbeat = time.time()
                self.get_or_create_mixer(room_id)
                self._recompute_room_topology_locked(room_id)
                return
            if old_room and old_room != room_id:
                old_mixer = self.room_mixers.get(old_room)
                self.rooms[old_room].discard(client_id)
                if not self.rooms[old_room]:
                    self.rooms.pop(old_room, None)
                    stale_mixer = self.room_mixers.pop(old_room, None)
                else:
                    stale_mixer = None
                    self._recompute_room_topology_locked(old_room)
            else:
                stale_mixer = None
            client.room = room_id
            client.last_heartbeat = time.time()
            self.rooms[room_id].add(client_id)
            self._recompute_room_topology_locked(room_id)

        if old_mixer is not None:
            old_mixer.remove_listener(client_id)
        if stale_mixer is not None:
            stale_mixer.stop()
        self.get_or_create_mixer(room_id)
        logging.info("%s joined room %s", client_id, room_id)

    def remove_client(self, client_id):
        mixer_to_stop = None
        with self.state_lock:
            client = self.clients.pop(client_id, None)
            self.sender_decoders.pop(client_id, None)
            if client and client.room:
                room_id = client.room
                room_members = self.rooms.get(room_id)
                if room_members is not None:
                    room_members.discard(client_id)
                    if not room_members:
                        self.rooms.pop(room_id, None)
                        mixer_to_stop = self.room_mixers.pop(room_id, None)
                    else:
                        self._recompute_room_topology_locked(room_id)
                mixer = self.room_mixers.get(room_id)
                if mixer is not None:
                    mixer.remove_source(client_id)
                    mixer.remove_listener(client_id)

        if mixer_to_stop is not None:
            mixer_to_stop.stop()
        if client is not None:
            logging.info("%s disconnected", client_id)

    async def prune_dead_clients(self):
        while self.running:
            self._prune_stale_clients_now()
            await asyncio.sleep(5.0)

    @staticmethod
    def extract_sender_id(packet):
        if not packet:
            return None
        if b":" not in packet:
            return None
        header = packet.split(b":", 1)[0]
        parts = header.split(b"|", 1)
        if not parts:
            return None
        sender_id = parts[0].decode(errors="ignore").strip()
        return sender_id or None

    @staticmethod
    def extract_opus_payload(packet):
        if not packet or b":" not in packet:
            return b""
        return packet.split(b":", 1)[1]

    def _get_or_create_decoder(self, sender_id):
        with self.state_lock:
            decoder = self.sender_decoders.get(sender_id)
        if decoder is not None:
            return decoder

        decoder = OpusCodec(
            rate=16000,
            channels=1,
            frame_size=MIX_FRAME_SAMPLES,
            create_encoder=False,
            create_decoder=True,
        )
        with self.state_lock:
            existing = self.sender_decoders.get(sender_id)
            if existing is not None:
                return existing
            self.sender_decoders[sender_id] = decoder
        return decoder

    def _sender_has_live_targets_locked(self, sender):
        if not sender.targets:
            return False
        if not sender.room:
            return False
        room_members = self.rooms.get(sender.room, set())
        for target_id in sender.targets:
            if target_id in room_members and target_id != sender.client_id:
                return True
        return False

    def process_audio_packet(self, packet, addr):
        if not packet:
            return
        if addr is None:
            addr = ("unknown", 0)

        sender_id = self.extract_sender_id(packet)
        if sender_id is None:
            self.malformed_count += 1
            if self.malformed_count % 100 == 1:
                logging.warning(
                    "Malformed audio packets=%s latest_from=%s",
                    self.malformed_count,
                    addr,
                )
            return

        with self.state_lock:
            sender = self.clients.get(sender_id)
            if sender is None:
                return
            sender.last_heartbeat = time.time()
            room_id = sender.room
            can_mix = self._sender_has_live_targets_locked(sender)

        self.packet_count[sender_id] += 1
        if not room_id or not can_mix:
            return

        opus_payload = self.extract_opus_payload(packet)
        if not opus_payload:
            return

        try:
            decoder = self._get_or_create_decoder(sender_id)
            pcm = decoder.decode(opus_payload)
        except Exception as err:
            logging.debug("Decode error from %s: %s", sender_id, err)
            return

        if not pcm:
            return

        mixer = self.get_or_create_mixer(room_id)
        mixer.add_pcm(sender_id, pcm)

    async def start_audio_server(self):
        logging.info("Audio UDP listening on port %s", AUDIO_PORT)
        while self.running:
            try:
                packet, addr = await self.loop.sock_recvfrom(self.udp_sock, 4096)
            except asyncio.CancelledError:
                raise
            except OSError:
                if self.running:
                    logging.exception("UDP receive loop error")
                break
            self.process_audio_packet(packet, addr)

    async def send_room_mixes(self):
        while self.running:
            with self.state_lock:
                room_entries = []
                for room_id, mixer in self.room_mixers.items():
                    listeners = []
                    for client_id in self.rooms.get(room_id, set()):
                        client = self.clients.get(client_id)
                        if client is not None:
                            hear_targets = None
                            if client.hear_targets is not None:
                                hear_targets = set(client.hear_targets)
                            listeners.append((client_id, client.addr, hear_targets))
                    room_entries.append((mixer, listeners))

            for mixer, listeners in room_entries:
                if not listeners:
                    continue
                mix_frames = mixer.drain_packets(limit=64)
                if not mix_frames:
                    continue
                for seq, source_items in mix_frames:
                    active_sender_ids = tuple(sender_id for sender_id, _pcm in source_items)
                    for listener_id, listener_addr, hear_targets in listeners:
                        active_others = [sid for sid in active_sender_ids if sid != listener_id]
                        if not active_others:
                            continue

                        # If listener configured explicit hear targets, suppress packets that
                        # include any speaker outside that allow-list.
                        if hear_targets is not None and any(sid not in hear_targets for sid in active_others):
                            continue

                        frames = [
                            pcm
                            for sender_id, pcm in source_items
                            if sender_id != listener_id
                        ]
                        if not frames:
                            continue

                        mixed_pcm = mixer._mix_pcm_frames(frames)
                        try:
                            encoder = mixer.get_or_create_listener_encoder(listener_id)
                            opus = encoder.encode(mixed_pcm)
                        except Exception as err:
                            logging.debug("Mix encode error for %s: %s", listener_id, err)
                            continue
                        if not opus:
                            continue
                        packet = f"MIXED|{seq}|".encode("ascii") + opus

                        try:
                            await self.loop.sock_sendto(self.udp_sock, packet, listener_addr)
                        except OSError as err:
                            logging.debug("UDP send error to %s: %s", listener_addr, err)

            await asyncio.sleep(0.002)

    async def shutdown(self):
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.running = False

        current_task = asyncio.current_task()
        pending = [task for task in self.tasks if task is not current_task and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.tasks.clear()

        if self.control_server is not None:
            self.control_server.close()
            try:
                await self.control_server.wait_closed()
            except Exception:
                pass
            self.control_server = None

        if self.udp_sock is not None:
            try:
                self.udp_sock.close()
            except OSError:
                pass
            self.udp_sock = None

        if self.discovery_thread is not None and self.discovery_thread.is_alive():
            self.discovery_thread.join(timeout=1.0)
        self.discovery_thread = None

        with self.state_lock:
            mixers = list(self.room_mixers.values())
            self.room_mixers.clear()
            self.clients.clear()
            self.rooms.clear()
            self.sender_decoders.clear()

        for mixer in mixers:
            mixer.stop()

        logging.info("Shutdown complete. UDP/TCP ports released.")

    async def start(self):
        logging.basicConfig(
            level=logging.INFO,
            format="[SERVER] %(asctime)s %(levelname)s %(message)s",
        )
        self.loop = asyncio.get_running_loop()
        self.running = True

        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
            _set_socket_dscp(self.udp_sock, IP_TOS_EF)
            self.udp_sock.bind(("0.0.0.0", AUDIO_PORT))
            self.udp_sock.setblocking(False)

            self.discovery_thread = threading.Thread(
                target=self.broadcast_server,
                daemon=True,
                name="discovery-broadcast",
            )
            self.discovery_thread.start()

            self.control_server = await asyncio.start_server(self.handle_control, "0.0.0.0", CONTROL_PORT)
            for sock in self.control_server.sockets or []:
                _set_socket_dscp(sock, IP_TOS_CS3)
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
            logging.info("Control TCP listening on port %s", CONTROL_PORT)

            self.tasks = [
                asyncio.create_task(self.prune_dead_clients()),
                asyncio.create_task(self.start_audio_server()),
                asyncio.create_task(self.send_room_mixes()),
            ]

            async with self.control_server:
                await self.control_server.serve_forever()
        except asyncio.CancelledError:
            raise
        finally:
            await self.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(VoiceServer().start())
    except KeyboardInterrupt:
        pass
