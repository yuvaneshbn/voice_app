import socket, threading, pyaudio

CHUNK = 1024
RATE = 16000

class AudioEngine:
    def __init__(self):
        self.audio = pyaudio.PyAudio()

        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.recv_sock.bind(("", 0))
        self.port = self.recv_sock.getsockname()[1]

        self.output = self.audio.open(
            format=pyaudio.paInt16, channels=1, rate=RATE, output=True
        )

        self.running = False
        self.hear_targets = set()
        threading.Thread(target=self.listen, daemon=True).start()

    def set_hear_targets(self, targets):
        self.hear_targets = targets

    def listen(self):
        while True:
            data, _ = self.recv_sock.recvfrom(4096)
            if b":" in data:
                sender, audio_data = data.split(b":", 1)
                sender_id = sender.decode()
                if sender_id in self.hear_targets:
                    self.output.write(audio_data)
            else:
                # old format, play anyway
                self.output.write(data)

    def start(self, server_ip):
        if self.running:
            return
        self.running = True

        self.input = self.audio.open(
            format=pyaudio.paInt16, channels=1, rate=RATE, input=True
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        def send():
            while self.running:
                data = self.input.read(CHUNK, exception_on_overflow=False)
                sock.sendto(data, (server_ip, 50002))

        threading.Thread(target=send, daemon=True).start()

    def stop(self):
        self.running = False
