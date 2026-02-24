import socket
import time

DISCOVERY_PORT = 50000
DISCOVERY_MAGIC = b"VOICE_SERVER"
DISCOVER_REQUEST = b"VOICE_DISCOVER"

class Network:
    def __init__(self):
        self.server_ip = None

    def discover(self, timeout=10):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Allow multiple clients on same machine
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Allow multiple processes to bind to same port (for discovery)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            # SO_REUSEPORT not available on Windows, that's OK
            pass

        # DSCP EF (46 << 2 = 184)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 184)
        except OSError:
            pass

        sock.settimeout(0.5)

        # Bind to port 50000 to receive server broadcasts
        try:
            sock.bind(("", DISCOVERY_PORT))
            print(f"[DISCOVERY] Bound to port {DISCOVERY_PORT}")
        except OSError as e:
            print(f"[DISCOVERY] Bind failed: {e} - using manual IP entry instead")
            return

        start = time.time()
        print("🔍 Discovering server...")

        while time.time() - start < timeout:
            try:
                data, addr = sock.recvfrom(1024)
                print(f"[DISCOVERY] Received from {addr}: {data!r}")
                if data == DISCOVERY_MAGIC:
                    self.server_ip = addr[0]
                    print("✅ Server found:", self.server_ip)
                    break
            except socket.timeout:
                # Active probe with multiple strategies for cross-subnet discovery
                try:
                    # Strategy 1: Broadcast to local subnet
                    print(f"[DISCOVERY] Sending broadcast probe to port {DISCOVERY_PORT}")
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.sendto(DISCOVER_REQUEST, ("<broadcast>", DISCOVERY_PORT))
                    
                    # Strategy 2: Direct probe to common gateway IPs
                    common_gateways = ["192.168.1.1", "192.168.0.1", "10.0.0.1", "192.168.1.255", "192.168.0.255"]
                    for gateway in common_gateways:
                        try:
                            sock.sendto(DISCOVER_REQUEST, (gateway, DISCOVERY_PORT))
                        except:
                            pass
                            
                except OSError as e:
                    print(f"[DISCOVERY] Probe send failed: {e}")

        sock.close()

        if not self.server_ip:
            print("❌ Server discovery timed out - will prompt for manual IP")
