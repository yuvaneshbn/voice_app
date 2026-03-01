import socket

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox
from PySide6.QtCore import Qt

CONTROL_PORT = 50001
DSCP_CS3 = 24
IP_TOS_CS3 = DSCP_CS3 << 2


class ServerIPDialog(QDialog):
    """Dialog for manual server IP entry"""
    
    def __init__(self):
        super().__init__()
        self.server_ip = None
        
        self.setWindowTitle("Voice App - Server Not Found")
        self.setGeometry(100, 100, 400, 150)
        
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("Server not found via broadcast."))
        layout.addWidget(QLabel("Enter server IP address:"))
        
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("192.168.0.X")
        layout.addWidget(self.ip_input)
        
        btn_layout = QHBoxLayout()
        
        ok_btn = QPushButton("Connect")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    
    def accept(self):
        ip = self.ip_input.text().strip()
        if ip:
            self.server_ip = ip
            super().accept()


class StartupDialog(QDialog):
    """Dialog to enter unique client name and confirm server"""
    
    def __init__(self, server_ip, audio_port):
        super().__init__()
        self.server_ip = server_ip
        self.audio_port = audio_port
        self.client_id = None
        
        self.setWindowTitle("Voice App - Client Setup")
        self.setGeometry(100, 100, 400, 200)
        
        layout = QVBoxLayout()
        
        # Server IP display
        layout.addWidget(QLabel(f"Server: {server_ip}"))
        layout.addWidget(QLabel(f"Audio Port: {audio_port}"))
        
        # Unique name input
        layout.addWidget(QLabel("Enter your unique name:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Alice / ControlRoom-1")
        self.name_input.returnPressed.connect(self.accept)
        layout.addWidget(self.name_input)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    
    def accept(self):
        candidate = self.name_input.text().strip()

        if not candidate:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Invalid Name")
            msg.setText("Name cannot be empty.")
            msg.exec()
            return

        if ":" in candidate:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Invalid Name")
            msg.setText("Name cannot contain ':'.")
            msg.exec()
            return

        if not self._is_name_available(candidate):
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Name Already In Use")
            msg.setText(f"'{candidate}' is already connected.")
            msg.setInformativeText("Please choose a different name.")
            msg.exec()
            return

        self.client_id = candidate
        super().accept()

    def _is_name_available(self, candidate):
        ctrl = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            ctrl.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, IP_TOS_CS3)
            ctrl.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        ctrl.settimeout(2.5)
        try:
            ctrl.connect((self.server_ip, CONTROL_PORT))
            ctrl.sendall(b"LIST\n")
            response = ctrl.recv(1024).decode(errors="ignore").strip()
            if not response:
                return True
            existing = {name.strip() for name in response.split(",") if name.strip()}
            return candidate not in existing
        except Exception:
            # If list check fails, registration flow in main.py still enforces uniqueness.
            return True
        finally:
            ctrl.close()
