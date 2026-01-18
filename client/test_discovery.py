from network import Network

net = Network()
net.discover()
print("Server IP:", net.server_ip)