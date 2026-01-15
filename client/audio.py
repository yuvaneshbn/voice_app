import socket, threading, pyaudio

CHUNK = 1024
RATE = 16000

class AudioEngine:
    def __init__(self):
        self.audio = pyaudio.PyAudio()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))
        self.port = self.sock.getsockname()[1]

        self.out = self.audio.open(format=pyaudio.paInt16, channels=1, rate=RATE, output=True)
        threading.Thread(target=self.listen, daemon=True).start()

        self.running = False

    def listen(self):
        while True:
            data, _ = self.sock.recvfrom(4096)
            self.out.write(data)

    def start(self, server_ip):
        if self.running:
            return
        self.running = True

        stream = self.audio.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        def send():
            while self.running:
                data = stream.read(CHUNK, exception_on_overflow=False)
                s.sendto(data, (server_ip, 50002))

        threading.Thread(target=send, daemon=True).start()

    def stop(self):
        self.running = False
