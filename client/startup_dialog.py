from PySide6.QtWidgets import QDialog, QPushButton, QVBoxLayout, QLabel, QMessageBox, QLineEdit, QHBoxLayout
import socket
import threading
import time

CONTROL_PORT = 50001


class ServerIPDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.server_ip = None

        self.setWindowTitle("Enter Server IP")
        self.setFixedSize(300, 150)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Server not found automatically. Enter server IP:"))

        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("e.g., 192.168.1.100")
        layout.addWidget(self.ip_edit)

        button_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept_ip)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def accept_ip(self):
        ip = self.ip_edit.text().strip()
        if ip:
            self.server_ip = ip
            self.accept()
        else:
            QMessageBox.warning(self, "Invalid", "Please enter a valid IP address")


class StartupDialog(QDialog):
    def __init__(self, server_ip, audio_port):
        super().__init__()
        self.server_ip = server_ip
        self.audio_port = audio_port
        self.client_id = None

        self.setWindowTitle("Select Client")
        self.setFixedSize(300, 250)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Choose your Client ID"))

        for i in range(1, 5):
            btn = QPushButton(f"Client {i}")
            btn.clicked.connect(lambda _, c=str(i): self.pick(c))
            layout.addWidget(btn)

        self.setLayout(layout)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1)

    def pick(self, cid):
        try:
            self.sock.sendto(
                f"REGISTER:{cid}:{self.audio_port}".encode(),
                (self.server_ip, CONTROL_PORT)
            )

            msg, _ = self.sock.recvfrom(1024)
            if msg == b"TAKEN":
                QMessageBox.warning(self, "Taken", f"Client {cid} already in use")
            else:
                self.client_id = cid
                self.accept()
        except:
            QMessageBox.warning(self, "Error", "Server not responding")
