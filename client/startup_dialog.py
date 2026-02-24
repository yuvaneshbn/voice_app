from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox
from PySide6.QtCore import Qt

CONTROL_PORT = 50001


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
    """Dialog to select client ID and confirm server"""
    
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
        
        # Client ID selection
        layout.addWidget(QLabel("Select Client ID:"))
        self.id_combo = QComboBox()
        self.id_combo.addItems(["1", "2", "3", "4"])
        layout.addWidget(self.id_combo)
        
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
        self.client_id = self.id_combo.currentText()
        
        # Validate client ID
        if not self.client_id or self.client_id not in ["1", "2", "3", "4"]:
            from PySide6.QtWidgets import QMessageBox
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Invalid Client ID")
            msg.setText("Please select a valid client ID (1, 2, 3, or 4).")
            msg.exec()
            return
        
        super().accept()
