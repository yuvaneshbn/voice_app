import sys, socket, threading
from PyQt5.QtWidgets import QApplication, QMainWindow
from voice_ui import Ui_project1
from network import Network
from audio import AudioEngine
from startup_dialog import StartupDialog, ServerIPDialog

ACTIVE = "QPushButton { background:#2ecc71; color:white; }"
INACTIVE = "QPushButton { background:#dddddd; }"
SELF = "QPushButton { background:#3498db; color:white; }"


class MainWindow(QMainWindow):
    def __init__(self, client_id, server_ip):
        super().__init__()
        self.ui = Ui_project1()
        self.ui.setupUi(self)
        self.setFixedSize(730, 475)

        self.my_id = client_id
        self.server_ip = server_ip
        self.targets = set()

        self.audio = AudioEngine()

        self.buttons = {
            "1": self.ui.client1btn,
            "2": self.ui.client2btn,
            "3": self.ui.client3btn,
            "4": self.ui.client4btn,
        }

        for cid, btn in self.buttons.items():
            btn.setCheckable(True)
            btn.setStyleSheet(INACTIVE)
            btn.clicked.connect(lambda _, c=cid: self.toggle(c))

        self.buttons[self.my_id].setStyleSheet(SELF)
        self.buttons[self.my_id].setEnabled(False)

        self.ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ctrl.bind(("", 0))

        threading.Thread(target=self.listen_control, daemon=True).start()

        self.ui.talkbtn.clicked.connect(self.broadcast)
        self.ui.statusbar.showMessage(f"You are Client {self.my_id}")

    def listen_control(self):
        while True:
            msg, _ = self.ctrl.recvfrom(1024)
            text = msg.decode()
            if text.startswith("SPEAKING:"):
                cid = text.split(":")[1]
                self.ui.statusbar.showMessage(f"Client {cid} is speaking")

    def toggle(self, cid):
        if cid == self.my_id:
            return

        btn = self.buttons[cid]
        if btn.isChecked():
            self.targets.add(cid)
            btn.setStyleSheet(ACTIVE)
        else:
            self.targets.discard(cid)
            btn.setStyleSheet(INACTIVE)

        self.update_targets()

    def update_targets(self):
        if self.targets:
            self.audio.start(self.server_ip)
        else:
            self.audio.stop()

        t = ",".join(self.targets)
        self.ctrl.sendto(f"TARGETS:{self.my_id}:{t}".encode(), (self.server_ip, 50001))

    def broadcast(self):
        self.targets = set(self.buttons.keys()) - {self.my_id}
        for c in self.targets:
            self.buttons[c].setChecked(True)
            self.buttons[c].setStyleSheet(ACTIVE)
        self.update_targets()


# ------------------ APP START ------------------

app = QApplication(sys.argv)

net = Network()
net.discover()

if not net.server_ip:
    ip_dlg = ServerIPDialog()
    if ip_dlg.exec_() == ip_dlg.Accepted:
        net.server_ip = ip_dlg.server_ip
    else:
        sys.exit(1)

audio = AudioEngine()

dlg = StartupDialog(net.server_ip, audio.port)
if dlg.exec_() != dlg.Accepted:
    sys.exit(0)

w = MainWindow(dlg.client_id, net.server_ip)
w.show()
sys.exit(app.exec_())
