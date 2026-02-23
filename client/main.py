import sys, socket
from PySide6.QtWidgets import QApplication, QMainWindow, QDialog
from voice_ui import Ui_project1
from network import Network
from audio import AudioEngine
from startup_dialog import StartupDialog, ServerIPDialog

ACTIVE = "QPushButton { background:#2ecc71; color:white; }"
INACTIVE = "QPushButton { background:#dddddd; }"
SELF = "QPushButton { background:#3498db; color:white; }"

class MainWindow(QMainWindow):
    def __init__(self, my_id, server_ip, audio):
        super().__init__()
        self.ui = Ui_project1()
        self.ui.setupUi(self)
        self.setFixedSize(730, 475)

        self.my_id = my_id
        self.server_ip = server_ip
        self.audio = audio
        self.audio.client_id = my_id  # Set client_id for audio engine
        self.targets = set()
        self.registration_successful = True  # Already registered at this point

        # TALK buttons (bottom row)
        self.talk_buttons = {
            "1": self.ui.cl1talkbtn,
            "2": self.ui.cl2talkbtn,
            "3": self.ui.cl3talkbtn,
            "4": self.ui.client4talkbtn,
        }

        self.hear_targets = set(self.talk_buttons.keys()) - {self.my_id}  # default to hear all except self

        # HEAR buttons (upper row)
        self.hear_buttons = {
            "1": self.ui.cl1hearbtn,
            "2": self.ui.cl2hearbtn,
            "3": self.ui.cl3hearbtn,
            "4": self.ui.cl4hearbtn,
        }

        # Enable all controls since registration is already successful
        self.enable_all_controls()

        for cid, btn in self.talk_buttons.items():
            btn.setCheckable(True)
            btn.setStyleSheet(INACTIVE)
            btn.clicked.connect(lambda _, c=cid: self.toggle_target(c))

        for cid, btn in self.hear_buttons.items():
            btn.setCheckable(True)
            btn.setStyleSheet(INACTIVE if cid != self.my_id else SELF)
            btn.setChecked(True if cid != self.my_id else False)
            btn.clicked.connect(lambda _, c=cid: self.toggle_hear(c))
            if cid == self.my_id:
                btn.setEnabled(False)

        self.talk_buttons[self.my_id].setStyleSheet(SELF)
        self.talk_buttons[self.my_id].setEnabled(False)

        self.ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ctrl.bind(("", 0))

        self.ui.talkbtn.clicked.connect(self.broadcast)
        self.ui.statusbar.showMessage(f"You are Client {self.my_id} - Connected")

        self.audio.set_hear_targets(self.hear_targets)

    def disable_all_controls(self):
        """Disable all communication controls"""
        for btn in self.talk_buttons.values():
            btn.setEnabled(False)
        for btn in self.hear_buttons.values():
            btn.setEnabled(False)
        self.ui.talkbtn.setEnabled(False)
        self.ui.statusbar.showMessage(f"You are Client {self.my_id} - Registering...")

    def enable_all_controls(self):
        """Enable all communication controls"""
        for cid, btn in self.talk_buttons.items():
            if cid != self.my_id:
                btn.setEnabled(True)
        for cid, btn in self.hear_buttons.items():
            if cid != self.my_id:
                btn.setEnabled(True)
        self.ui.talkbtn.setEnabled(True)

    def toggle_target(self, cid):
        if cid == self.my_id or not self.registration_successful:
            return

        btn = self.talk_buttons[cid]
        if btn.isChecked():
            self.targets.add(cid)
            btn.setStyleSheet(ACTIVE)
        else:
            self.targets.discard(cid)
            btn.setStyleSheet(INACTIVE)

        self.update_targets()

    def toggle_hear(self, cid):
        if cid == self.my_id or not self.registration_successful:
            return

        btn = self.hear_buttons[cid]
        if btn.isChecked():
            self.hear_targets.add(cid)
            btn.setStyleSheet(ACTIVE)
        else:
            self.hear_targets.discard(cid)
            btn.setStyleSheet(INACTIVE)

        self.audio.set_hear_targets(self.hear_targets)

    def update_targets(self):
        if not self.registration_successful:
            return
            
        if self.targets and not self.audio.running:
            self.audio.start(self.server_ip)
        elif not self.targets and self.audio.running:
            self.audio.stop()

        t = ",".join(self.targets)
        self.ctrl.sendto(f"TALK:{self.my_id}:{t}".encode(), (self.server_ip, 50001))

    def broadcast(self):
        if not self.registration_successful:
            return
            
        # Toggle broadcast mode
        all_targets = set(self.talk_buttons.keys()) - {self.my_id}
        
        if self.targets == all_targets:
            # Currently broadcasting - turn it off
            self.targets = set()
            for c in all_targets:
                self.talk_buttons[c].setChecked(False)
                self.talk_buttons[c].setStyleSheet(INACTIVE)
        else:
            # Not broadcasting - turn it on
            self.targets = all_targets
            for c in all_targets:
                self.talk_buttons[c].setChecked(True)
                self.talk_buttons[c].setStyleSheet(ACTIVE)
        
        self.update_targets()

    def closeEvent(self, event):
        """Clean shutdown - unregister from server"""
        try:
            unregister_msg = f"UNREGISTER:{self.my_id}".encode()
            self.ctrl.sendto(unregister_msg, (self.server_ip, 50001))
            print(f"[CLIENT] Sent unregistration: {self.my_id}")
        except Exception as e:
            print(f"[CLIENT] ❌ Unregistration error: {e}")
        
        self.audio.stop()
        event.accept()

def register_client_with_server(client_id, server_ip, audio_port):
    """Register client with server BEFORE creating AudioEngine or MainWindow"""
    try:
        ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ctrl.bind(("", 0))
        
        register_msg = f"REGISTER:{client_id}:{audio_port}".encode()
        ctrl.sendto(register_msg, (server_ip, 50001))
        print(f"[CLIENT] Sent registration: {client_id} on port {audio_port}")
        
        # Wait for response
        ctrl.settimeout(5.0)
        try:
            response, _ = ctrl.recvfrom(1024)
            if response == b"OK":
                print(f"[CLIENT] ✅ Registration successful for client {client_id}")
                ctrl.close()
                return True
            elif response == b"TAKEN":
                print(f"[CLIENT] ❌ Client ID {client_id} already taken")
                ctrl.close()
                return False
            else:
                print(f"[CLIENT] ⚠️ Unexpected registration response: {response}")
                ctrl.close()
                return False
        except socket.timeout:
            print(f"[CLIENT] ⚠️ Registration timeout for client {client_id}")
            ctrl.close()
            return False
    except Exception as e:
        print(f"[CLIENT] ❌ Registration error: {e}")
        return False

def main():
    print("=" * 50)
    print("🎤 VOICE CHAT CLIENT STARTING")
    print("=" * 50)
    
    app = QApplication(sys.argv)

    # Discover server
    net = Network()
    print("[CLIENT] 🔍 Discovering server...")
    net.discover()

    if not net.server_ip:
        print("[CLIENT] ❌ Server not found, prompting for manual IP...")
        dlg_ip = ServerIPDialog()
        if dlg_ip.exec() == QDialog.Accepted:
            net.server_ip = dlg_ip.server_ip
            print(f"[CLIENT] 📡 Using manual server IP: {net.server_ip}")
        else:
            print("[CLIENT] 🛑 User cancelled, exiting")
            sys.exit(0)
    else:
        print(f"[CLIENT] ✅ Server found at: {net.server_ip}")

    # Get client ID from user FIRST
    dlg = StartupDialog(net.server_ip, 0)  # audio_port not needed yet
    if not dlg.exec():
        print("[CLIENT] 🛑 User cancelled client setup, exiting")
        sys.exit(0)
    
    client_id = dlg.client_id
    print(f"[CLIENT] 👤 Selected Client ID: {client_id}")
    
    # Create AudioEngine for this client
    audio = AudioEngine()
    audio_port = audio.port
    print(f"[CLIENT] 🎧 Audio engine initialized on port {audio_port}")
    
    # REGISTER WITH SERVER BEFORE ANYTHING ELSE
    print(f"[CLIENT] 🔄 Registering with server...")
    if not register_client_with_server(client_id, net.server_ip, audio_port):
        # Registration failed - show error and exit
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Registration Failed")
        msg.setText(f"Client ID {client_id} is already in use or registration failed!")
        msg.setInformativeText("Please choose a different client ID and try again.")
        msg.exec()
        audio.stop()  # Clean up audio
        sys.exit(1)
    
    # Registration successful - now create the UI
    print(f"[CLIENT] ✅ Registration successful - starting UI...")
    
    # Main window
    try:
        w = MainWindow(client_id, net.server_ip, audio)
        w.show()
        print("[CLIENT] ✅ Client ready!")
        sys.exit(app.exec())
    except Exception as e:
        print(f"[CLIENT] ❌ Failed to start main window: {e}")
        audio.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
