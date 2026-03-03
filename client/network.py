import socket
import time

DISCOVERY_PORT = 50000
DISCOVERY_MAGIC = b"VOICE_SERVER"
DISCOVER_REQUEST = b"VOICE_DISCOVER"
DSCP_CS3 = 24
IP_TOS_CS3 = DSCP_CS3 << 2


class Network:
    def __init__(self):
        self.server_ip = None

    def discover(self, timeout=10):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Allow multiple clients on same machine.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Allow multiple processes to bind to same port (for discovery).
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                # SO_REUSEPORT is not available on Windows.
                pass

            # Discovery is control-plane traffic, not real-time media.
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, IP_TOS_CS3)
            except OSError:
                pass

            sock.settimeout(0.5)

            # Bind to receive server broadcasts.
            try:
                sock.bind(("", DISCOVERY_PORT))
                print(f"[DISCOVERY] Bound to port {DISCOVERY_PORT}")
            except OSError as e:
                print(f"[DISCOVERY] Bind failed: {e} - using manual IP entry instead")
                return

            start = time.time()
            print("[DISCOVERY] Discovering server...")

            while time.time() - start < timeout:
                try:
                    data, addr = sock.recvfrom(1024)
                    print(f"[DISCOVERY] Received from {addr}: {data!r}")
                    if data == DISCOVERY_MAGIC:
                        self.server_ip = addr[0]
                        print(f"[DISCOVERY] Server found: {self.server_ip}")
                        break
                except socket.timeout:
                    # Active probe with multiple strategies for cross-subnet discovery.
                    try:
                        print(f"[DISCOVERY] Sending broadcast probe to port {DISCOVERY_PORT}")
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        sock.sendto(DISCOVER_REQUEST, ("<broadcast>", DISCOVERY_PORT))

                        common_gateways = [
                            "192.168.1.1",
                            "192.168.0.1",
                            "10.0.0.1",
                            "192.168.1.255",
                            "192.168.0.255",
                        ]
                        for gateway in common_gateways:
                            try:
                                sock.sendto(DISCOVER_REQUEST, (gateway, DISCOVERY_PORT))
                            except OSError:
                                pass
                    except OSError as e:
                        print(f"[DISCOVERY] Probe send failed: {e}")
        finally:
            sock.close()

        if not self.server_ip:
            print("[DISCOVERY] Server discovery timed out - will prompt for manual IP")
