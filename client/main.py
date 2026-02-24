import faulthandler
import socket
import sys
import time
import threading
import traceback

from PySide6.QtWidgets import QApplication, QDialog, QMainWindow

from audio import AudioEngine
from network import Network
from startup_dialog import ServerIPDialog, StartupDialog
from voice_ui import Ui_project1

ACTIVE = "QPushButton { background:#2ecc71; color:white; }"
INACTIVE = "QPushButton { background:#dddddd; }"
SELF = "QPushButton { background:#3498db; color:white; }"
CONTROL_PORT = 50001
DEFAULT_ROOM = "main"


def send_control_command(server_ip, command, timeout=5.0):
    ctrl = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ctrl.settimeout(timeout)
    try:
        ctrl.connect((server_ip, CONTROL_PORT))
        ctrl.sendall((command + "\n").encode())
        response = ctrl.recv(1024).decode(errors="ignore").strip()
        return True, response
    except Exception as e:
        return False, str(e)
    finally:
        ctrl.close()


class MainWindow(QMainWindow):
    def __init__(self, my_id, server_ip, audio):
        super().__init__()
        self.ui = Ui_project1()
        self.ui.setupUi(self)
        self.setFixedSize(730, 475)

        self.my_id = my_id
        self.server_ip = server_ip
        self.audio = audio
        self.audio.client_id = my_id
        self.targets = set()
        self.registration_successful = True

        self.talk_buttons = {
            "1": self.ui.cl1talkbtn,
            "2": self.ui.cl2talkbtn,
            "3": self.ui.cl3talkbtn,
            "4": self.ui.client4talkbtn,
        }

        self.hear_targets = set(self.talk_buttons.keys()) - {self.my_id}

        self.hear_buttons = {
            "1": self.ui.cl1hearbtn,
            "2": self.ui.cl2hearbtn,
            "3": self.ui.cl3hearbtn,
            "4": self.ui.cl4hearbtn,
        }

        self.enable_all_controls()

        for cid, btn in self.talk_buttons.items():
            btn.setCheckable(True)
            btn.setStyleSheet(INACTIVE)
            btn.clicked.connect(lambda _, c=cid: self.toggle_target(c))

        for cid, btn in self.hear_buttons.items():
            btn.setCheckable(True)
            btn.setStyleSheet(INACTIVE if cid != self.my_id else SELF)
            btn.setChecked(cid != self.my_id)
            btn.clicked.connect(lambda _, c=cid: self.toggle_hear(c))
            if cid == self.my_id:
                btn.setEnabled(False)

        self.talk_buttons[self.my_id].setStyleSheet(SELF)
        self.talk_buttons[self.my_id].setEnabled(False)

        self.ui.talkbtn.clicked.connect(self.broadcast)
        self.ui.statusbar.showMessage(f"You are Client {self.my_id} - Connected")

        self.audio.set_hear_targets(self.hear_targets)

    def disable_all_controls(self):
        for btn in self.talk_buttons.values():
            btn.setEnabled(False)
        for btn in self.hear_buttons.values():
            btn.setEnabled(False)
        self.ui.talkbtn.setEnabled(False)
        self.ui.statusbar.showMessage(f"You are Client {self.my_id} - Registering...")

    def enable_all_controls(self):
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

        # Keep capture thread stable once started; stopping/restarting rapidly on UI
        # target changes caused send-thread generation churn in practice.
        if self.targets and not self.audio.running:
            self.audio.start(self.server_ip)

        targets = ",".join(sorted(self.targets))
        ok, response = send_control_command(self.server_ip, f"TARGETS:{self.my_id}:{targets}")
        if not ok or response != "OK":
            print(f"[CLIENT] Failed to update targets: {response}")

    def broadcast(self):
        if not self.registration_successful:
            return

        all_targets = set(self.talk_buttons.keys()) - {self.my_id}

        if self.targets == all_targets:
            self.targets = set()
            for cid in all_targets:
                self.talk_buttons[cid].setChecked(False)
                self.talk_buttons[cid].setStyleSheet(INACTIVE)
        else:
            self.targets = all_targets
            for cid in all_targets:
                self.talk_buttons[cid].setChecked(True)
                self.talk_buttons[cid].setStyleSheet(ACTIVE)

        self.update_targets()

    def closeEvent(self, event):
        try:
            send_control_command(self.server_ip, f"UNREGISTER:{self.my_id}")
            print(f"[CLIENT] Sent unregistration: {self.my_id}")
        except Exception as e:
            print(f"[CLIENT] Unregistration error: {e}")

        self.audio.stop()
        event.accept()


def register_client_with_server(client_id, server_ip, audio_port):
    try:
        ok, response = send_control_command(server_ip, f"REGISTER:{client_id}:{audio_port}")
        if not ok:
            print(f"[CLIENT] Registration error: {response}")
            return False

        if response == "TAKEN":
            print(f"[CLIENT] Client ID {client_id} already taken")
            return False

        if response != "OK":
            print(f"[CLIENT] Unexpected registration response: {response}")
            return False

        join_ok, join_response = send_control_command(server_ip, f"JOIN:{client_id}:{DEFAULT_ROOM}")
        if not join_ok or join_response != "OK":
            print(f"[CLIENT] JOIN failed for client {client_id}: {join_response}")
            return False

        print(f"[CLIENT] Registration successful for client {client_id}")
        return True
    except Exception as e:
        print(f"[CLIENT] Registration error: {e}")
        return False


def main():
    crash_log = None
    try:
        crash_log = open("client_crash.log", "a", encoding="utf-8")
    except Exception:
        crash_log = None

    def _log_unhandled(exc_type, exc_value, exc_tb):
        line = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            if crash_log is not None:
                crash_log.write("\n=== Unhandled Exception ===\n")
                crash_log.write(line)
                crash_log.flush()
        except Exception:
            pass
        if sys.__stderr__ is not None:
            sys.__stderr__.write(line)

    def _thread_excepthook(args):
        _log_unhandled(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _log_unhandled
    threading.excepthook = _thread_excepthook

    try:
        if crash_log is not None:
            faulthandler.enable(file=crash_log, all_threads=True)
        elif sys.stderr is not None:
            faulthandler.enable(all_threads=True)
    except Exception:
        pass
    print("=" * 50)
    print("VOICE CHAT CLIENT STARTING")
    print("=" * 50)

    app = QApplication(sys.argv)

    net = Network()
    print("[CLIENT] Discovering server...")
    net.discover()

    if not net.server_ip:
        print("[CLIENT] Server not found, prompting for manual IP...")
        dlg_ip = ServerIPDialog()
        if dlg_ip.exec() == QDialog.Accepted:
            net.server_ip = dlg_ip.server_ip
            print(f"[CLIENT] Using manual server IP: {net.server_ip}")
        else:
            print("[CLIENT] User cancelled, exiting")
            sys.exit(0)
    else:
        print(f"[CLIENT] Server found at: {net.server_ip}")

    dlg = StartupDialog(net.server_ip, 0)
    if not dlg.exec():
        print("[CLIENT] User cancelled client setup, exiting")
        sys.exit(0)

    client_id = dlg.client_id
    print(f"[CLIENT] Selected Client ID: {client_id}")

    audio = AudioEngine()
    audio_port = audio.port
    print(f"[CLIENT] Audio engine initialized on port {audio_port}")

    print("[CLIENT] Registering with server...")
    if not register_client_with_server(client_id, net.server_ip, audio_port):
        from PySide6.QtWidgets import QMessageBox

        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Registration Failed")
        msg.setText(f"Client ID {client_id} is already in use or registration failed!")
        msg.setInformativeText("Please choose a different client ID and try again.")
        msg.exec()
        audio.stop()
        sys.exit(1)

    print("[CLIENT] Registration successful - starting UI...")

    try:
        w = MainWindow(client_id, net.server_ip, audio)
        w.show()
        print("[CLIENT] Client ready")
        sys.exit(app.exec())
    except Exception as e:
        print(f"[CLIENT] Failed to start main window: {e}")
        audio.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
