import socket

DISCOVERY_PORT = 50000

class Network:
    def __init__(self):
        self.server_ip = None

    def discover(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(10)  # Timeout after 10 seconds

        # Bind to the discovery port to receive broadcasts
        sock.bind(("", DISCOVERY_PORT))

        # Listen for broadcast
        try:
            while not self.server_ip:
                msg, addr = sock.recvfrom(1024)
                if msg == b"VOICE_SERVER":
                    self.server_ip = addr[0]
                    print("Server found:", self.server_ip)
        except socket.timeout:
            print("Server discovery timed out. No server found on network.")
