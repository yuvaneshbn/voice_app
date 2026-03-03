import faulthandler
import os
import socket
import sys
import threading
import time
import traceback

from PySide6.QtCore import QFile, QIODevice, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from audio import AudioEngine
from network import Network
from startup_dialog import ServerIPDialog, StartupDialog

CONTROL_PORT = 50001
DEFAULT_ROOM = "main"
REGISTER_SECRET = os.getenv("VOICE_REGISTER_SECRET", "mysecret")
DSCP_EF = 46
DSCP_CS3 = 24
IP_TOS_EF = DSCP_EF << 2
IP_TOS_CS3 = DSCP_CS3 << 2

CLIENT_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(CLIENT_DIR, "ui")
MAIN_WINDOW_UI = os.path.join(UI_DIR, "main_window.ui")
PARTICIPANT_ITEM_UI = os.path.join(UI_DIR, "participant_item.ui")
SETTINGS_DIALOG_UI = os.path.join(UI_DIR, "settings_dialog.ui")
VOLUME_CONTROL_UI = os.path.join(UI_DIR, "volume_control.ui")
APP_ICON_PATH = os.path.join(CLIENT_DIR, "technical-support.ico")


def _sort_client_ids(client_id):
    if str(client_id).isdigit():
        return (0, int(client_id))
    return (1, str(client_id))


def _parse_client_list_response(response):
    if not response:
        return []
    if "\n" in response:
        return [cid.strip() for cid in response.splitlines() if cid.strip()]
    return [cid.strip() for cid in response.split(",") if cid.strip()]


def load_ui_widget(ui_path, parent=None):
    loader = QUiLoader()
    ui_file = QFile(ui_path)
    if not ui_file.open(QIODevice.ReadOnly):
        raise RuntimeError(f"Unable to open UI file: {ui_path}")
    try:
        widget = loader.load(ui_file, parent)
    finally:
        ui_file.close()
    if widget is None:
        raise RuntimeError(f"Unable to load UI file: {ui_path}")
    return widget


def require_child(parent, widget_type, object_name):
    widget = parent.findChild(widget_type, object_name)
    if widget is None:
        raise RuntimeError(
            f"Missing required widget '{object_name}' in UI loaded into {type(parent).__name__}"
        )
    return widget


def find_first_child(parent, widget_type, *object_names):
    for name in object_names:
        widget = parent.findChild(widget_type, name)
        if widget is not None:
            return widget
    return None


def _set_socket_dscp(sock, ip_tos):
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, ip_tos)
    except OSError:
        pass


def send_control_command(server_ip, command, timeout=5.0):
    ctrl = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _set_socket_dscp(ctrl, IP_TOS_CS3)
    try:
        ctrl.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
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


def join_room(server_ip, client_id, room_id=DEFAULT_ROOM):
    ok, join_response = send_control_command(server_ip, f"JOIN:{client_id}:{room_id}")
    if not ok or not join_response.startswith("OK"):
        return False, None, join_response
    multicast_addr = None
    if ":" in join_response:
        _, multicast_addr = join_response.split(":", 1)
    return True, multicast_addr, join_response


class VolumeControlPanel:
    def __init__(self, audio, parent=None):
        self.audio = audio
        self.widget = load_ui_widget(VOLUME_CONTROL_UI, parent)

        self.master_slider = require_child(self.widget, QSlider, "masterSlider")
        self.gain_slider = require_child(self.widget, QSlider, "gainSlider")
        self.noise_suppression_label = require_child(self.widget, QLabel, "noiseSuppressionLabel")
        self.noise_suppression_slider = require_child(self.widget, QSlider, "noiseSuppressionSlider")
        self.noise_suppression_enable_checkbox = require_child(self.widget, QCheckBox, "noiseSuppressionEnableCheckbox")
        self.echo_checkbox = require_child(self.widget, QCheckBox, "echoCheckbox")
        self.test_mic_button = require_child(self.widget, QPushButton, "testMicButton")
        self.test_status_label = require_child(self.widget, QLabel, "testStatusLabel")
        self.mic_level_bar = require_child(self.widget, QProgressBar, "micLevelBar")

        self._configure_controls()
        self._wire_signals()

    def _configure_controls(self):
        for slider, default_value in (
            (self.master_slider, int(self.audio.master_volume * 100)),
            (self.noise_suppression_slider, int(self.audio.noise_suppression)),
        ):
            slider.setMinimum(0)
            slider.setMaximum(100)
            slider.setValue(default_value)

        self.gain_slider.setValue(int(self.audio.tx_gain_db))
        self.noise_suppression_enable_checkbox.setChecked(self.audio.noise_suppression_enabled)
        self._sync_noise_suppression_controls()
        self.echo_checkbox.setChecked(self.audio.echo_enabled)
        self.echo_checkbox.setEnabled(self.audio.echo is not None)
        self.mic_level_bar.setMinimum(0)
        self.mic_level_bar.setMaximum(100)
        self.mic_level_bar.setValue(0)

    def _wire_signals(self):
        self.master_slider.valueChanged.connect(self.audio.set_master_volume)
        self.gain_slider.valueChanged.connect(self.audio.set_gain_db)
        self.noise_suppression_slider.valueChanged.connect(self.audio.set_noise_suppression)
        self.noise_suppression_enable_checkbox.toggled.connect(self._on_noise_suppression_toggled)
        self.echo_checkbox.toggled.connect(self.audio.set_echo_enabled)
        self.test_mic_button.clicked.connect(self._test_microphone)

    def _test_microphone(self):
        level = self.audio.test_microphone_level(0.8)
        self.set_mic_level(level)
        self.test_status_label.setText(f"Mic level: {level}%")

    def set_mic_level(self, level):
        self.mic_level_bar.setValue(max(0, min(100, int(level))))

    def _sync_noise_suppression_controls(self):
        enabled = bool(self.audio.noise_suppression_enabled)
        if self.noise_suppression_slider is not None:
            self.noise_suppression_slider.setEnabled(enabled)
        if self.noise_suppression_label is not None:
            self.noise_suppression_label.setEnabled(enabled)

    def _on_noise_suppression_toggled(self, checked):
        self.audio.set_noise_suppression_enabled(checked)
        self._sync_noise_suppression_controls()


class ParticipantRow:
    def __init__(self, client_id, is_self, talk_checked, mute_checked, talk_cb, mute_cb, parent=None):
        self.client_id = str(client_id)
        self.is_self = is_self
        self.widget = load_ui_widget(PARTICIPANT_ITEM_UI, parent)

        self.name_label = require_child(self.widget, QLabel, "participantName")
        self.talk_checkbox = require_child(self.widget, QCheckBox, "talkCheckbox")
        self.mute_checkbox = find_first_child(
            self.widget,
            QCheckBox,
            "muteCheckbox",
            "hearCheckbox",  # legacy UI object name
        )
        if self.mute_checkbox is None:
            raise RuntimeError("Missing required participant mute checkbox (muteCheckbox/hearCheckbox)")
        self.mic_status_label = require_child(self.widget, QLabel, "micStatusLabel")
        self.volume_bar = require_child(self.widget, QProgressBar, "participantVolumeBar")

        name_text = f"Client {self.client_id}"
        if self.is_self:
            name_text += " (You)"
        self.name_label.setText(name_text)

        self.talk_checkbox.setChecked(bool(talk_checked))
        self.talk_checkbox.setEnabled(not self.is_self)

        self.mute_checkbox.setText("Mute")
        self.mute_checkbox.setChecked(bool(mute_checked))
        self.mute_checkbox.setEnabled(not self.is_self)

        self.mic_status_label.setText("Mic: Off")
        self.volume_bar.setMinimum(0)
        self.volume_bar.setMaximum(100)
        self.volume_bar.setValue(0)

        self.talk_checkbox.toggled.connect(lambda checked: talk_cb(self.client_id, checked))
        self.mute_checkbox.toggled.connect(lambda checked: mute_cb(self.client_id, checked))

    def set_talk_checked(self, enabled):
        if self.talk_checkbox.isChecked() != bool(enabled):
            self.talk_checkbox.blockSignals(True)
            self.talk_checkbox.setChecked(bool(enabled))
            self.talk_checkbox.blockSignals(False)

    def set_mute_checked(self, enabled):
        if self.mute_checkbox.isChecked() != bool(enabled):
            self.mute_checkbox.blockSignals(True)
            self.mute_checkbox.setChecked(bool(enabled))
            self.mute_checkbox.blockSignals(False)

    def set_volume(self, value):
        self.volume_bar.setValue(max(0, min(100, int(value))))

    def set_mic_status(self, is_on):
        self.mic_status_label.setText("Mic: On" if is_on else "Mic: Off")


class SettingsDialog(QDialog):
    def __init__(self, audio, server_ip, reconnect_cb, parent=None):
        super().__init__(parent)
        self.audio = audio
        self.server_ip = server_ip
        self.reconnect_cb = reconnect_cb
        self._populating_devices = False

        self.form = load_ui_widget(SETTINGS_DIALOG_UI, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.form)

        self.setWindowTitle(self.form.windowTitle())
        if os.path.exists(APP_ICON_PATH):
            self.setWindowIcon(QIcon(APP_ICON_PATH))

        self.input_device_combo = require_child(self.form, QComboBox, "inputDeviceCombo")
        self.output_device_combo = require_child(self.form, QComboBox, "outputDeviceCombo")
        self.server_ip_value = require_child(self.form, QLabel, "serverIpValue")
        self.reconnect_button = require_child(self.form, QPushButton, "reconnectButton")
        self.save_close_button = require_child(self.form, QPushButton, "saveCloseButton")
        self.cancel_button = require_child(self.form, QPushButton, "cancelButton")

        self.advanced_audio_layout = self.form.findChild(QVBoxLayout, "advancedAudioLayout")
        if self.advanced_audio_layout is None:
            advanced_group = self.form.findChild(QWidget, "advancedAudioGroup")
            if advanced_group is not None:
                self.advanced_audio_layout = advanced_group.layout()
        self.volume_hint = self.form.findChild(QLabel, "volumeControlHint")

        self.volume_controls = VolumeControlPanel(self.audio, self.form)
        if self.volume_hint is not None:
            self.volume_hint.setParent(None)
        if self.advanced_audio_layout is not None:
            self.advanced_audio_layout.addWidget(self.volume_controls.widget)

        self.server_ip_value.setText(self.server_ip)

        self._populate_devices()

        self.input_device_combo.currentIndexChanged.connect(self._on_input_device_changed)
        self.output_device_combo.currentIndexChanged.connect(self._on_output_device_changed)
        self.reconnect_button.clicked.connect(self._reconnect)
        self.save_close_button.clicked.connect(self._save_and_close)
        self.cancel_button.clicked.connect(self.reject)

    def _populate_devices(self):
        self._populating_devices = True
        self.input_device_combo.blockSignals(True)
        self.output_device_combo.blockSignals(True)
        self.input_device_combo.clear()
        self.output_device_combo.clear()

        input_devices = self.audio.list_input_devices()
        output_devices = self.audio.list_output_devices()

        for idx, name in input_devices:
            self.input_device_combo.addItem(name, idx)

        for idx, name in output_devices:
            self.output_device_combo.addItem(name, idx)

        if self.audio.input_device_index is not None:
            pos = self.input_device_combo.findData(self.audio.input_device_index)
            if pos >= 0:
                self.input_device_combo.setCurrentIndex(pos)

        if self.audio.output_device_index is not None:
            pos = self.output_device_combo.findData(self.audio.output_device_index)
            if pos >= 0:
                self.output_device_combo.setCurrentIndex(pos)

        self.input_device_combo.blockSignals(False)
        self.output_device_combo.blockSignals(False)
        self._populating_devices = False

    def _reconnect(self):
        ok, message = self.reconnect_cb()
        box = QMessageBox(self)
        box.setWindowTitle("Reconnect")
        if ok:
            box.setIcon(QMessageBox.Information)
            box.setText("Reconnected successfully.")
            if message:
                box.setInformativeText(message)
        else:
            box.setIcon(QMessageBox.Warning)
            box.setText("Reconnect failed.")
            if message:
                box.setInformativeText(message)
        box.exec()

    def _save_and_close(self):
        self._on_input_device_changed()
        self._on_output_device_changed()
        self.accept()

    def _on_input_device_changed(self):
        if self._populating_devices:
            return
        input_device = self.input_device_combo.currentData()
        if input_device is not None:
            self.audio.set_input_device(input_device)

    def _on_output_device_changed(self):
        if self._populating_devices:
            return
        output_device = self.output_device_combo.currentData()
        if output_device is not None:
            self.audio.set_output_device(output_device)


class MainWindow(QMainWindow):
    heartbeat_result = Signal(bool)

    def __init__(self, my_id, server_ip, audio):
        super().__init__()

        self.my_id = str(my_id)
        self.server_ip = server_ip
        self.audio = audio
        self.audio.client_id = self.my_id

        self.targets = set()
        self.muted_participants = set()
        self.hear_targets = set()
        self.participant_rows = {}
        self.speaker_state = {}

        self.connected = True
        self.registration_successful = True
        self._heartbeat_failures = 0
        self._hb_stop = threading.Event()
        self._unregistered = False
        self._cleaned_up = False

        root = load_ui_widget(MAIN_WINDOW_UI, self)
        self.root = root
        self.setCentralWidget(root)
        self.setWindowTitle(root.windowTitle())
        if os.path.exists(APP_ICON_PATH):
            self.setWindowIcon(QIcon(APP_ICON_PATH))

        self.room_combo = require_child(root, QComboBox, "roomCombo")
        self.join_leave_button = require_child(root, QPushButton, "joinLeaveButton")
        self.refresh_button = require_child(root, QPushButton, "refreshButton")
        self.connection_indicator = require_child(root, QLabel, "connectionIndicator")

        self.search_input = require_child(root, QLineEdit, "searchInput")
        self.participant_list = require_child(root, QListWidget, "participantList")
        self.count_label = require_child(root, QLabel, "countLabel")

        self.active_speakers_label = require_child(root, QLabel, "activeSpeakersLabel")
        self.speaker_log_list = require_child(root, QListWidget, "speakerLogList")
        self.system_level_bar = require_child(root, QProgressBar, "systemLevelBar")

        self.controls_layout = root.findChild(QVBoxLayout, "controlsPlaceholderLayout")
        if self.controls_layout is None:
            controls_group = root.findChild(QWidget, "myControlsGroup")
            if controls_group is not None:
                self.controls_layout = controls_group.layout()
        if self.controls_layout is None:
            raise RuntimeError("Missing required controls layout: controlsPlaceholderLayout")
        self.controls_hint = root.findChild(QLabel, "controlsHint")

        self.mute_button = require_child(root, QPushButton, "muteButton")
        self.broadcast_button = require_child(root, QPushButton, "broadcastButton")
        self.settings_button = require_child(root, QPushButton, "settingsButton")

        self.warning_label = require_child(root, QLabel, "warningLabel")
        self.main_status_bar = require_child(root, QStatusBar, "mainStatusBar")

        self.volume_controls = VolumeControlPanel(self.audio, root)
        if self.controls_hint is not None:
            self.controls_hint.setParent(None)
        if self.controls_layout is not None:
            self.controls_layout.addWidget(self.volume_controls.widget)

        self.room_combo.clear()
        self.room_combo.addItem(DEFAULT_ROOM)
        self.room_combo.setCurrentText(DEFAULT_ROOM)
        self.room_combo.setEnabled(False)

        self.join_leave_button.setText("Leave Room")
        self.join_leave_button.clicked.connect(self.leave_room_and_exit)

        self.refresh_button.clicked.connect(self.refresh_participants)
        self.search_input.textChanged.connect(self.apply_search_filter)

        self.mute_button.setCheckable(True)
        self.mute_button.toggled.connect(self.toggle_self_mute)
        self.broadcast_button.setCheckable(True)
        self.broadcast_button.setChecked(False)
        self.broadcast_button.setText("Broadcast Off")
        self.broadcast_button.toggled.connect(self.toggle_broadcast)
        self.settings_button.clicked.connect(self.open_settings)

        self._stop_capture_timer = QTimer(self)
        self._stop_capture_timer.setSingleShot(True)
        self._stop_capture_timer.setInterval(1200)
        self._stop_capture_timer.timeout.connect(self._stop_capture_if_idle)

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(200)
        self._ui_timer.timeout.connect(self.update_live_ui)
        self._ui_timer.start()
        self.heartbeat_result.connect(self._handle_heartbeat)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(3000)
        self._auto_refresh_timer.timeout.connect(lambda: self.refresh_participants(silent=True))
        self._auto_refresh_timer.start()

        self.system_level_bar.setMinimum(0)
        self.system_level_bar.setMaximum(100)
        self.system_level_bar.setValue(0)

        self.main_status_bar.showMessage(f"Client {self.my_id} connected to {self.server_ip}")
        self._set_connected_state(True)

        self.refresh_participants()
        threading.Thread(target=self.heartbeat_loop, daemon=True, name="heartbeat").start()

    def _set_connected_state(self, connected, detail=""):
        self.connected = bool(connected)
        if self.connected:
            self.connection_indicator.setText("Connected")
            self.connection_indicator.setStyleSheet("color:#1E8E3E; font-weight:bold;")
            if detail:
                self.main_status_bar.showMessage(detail)
            self.warning_label.setText("")
        else:
            self.connection_indicator.setText("Disconnected")
            self.connection_indicator.setStyleSheet("color:#C62828; font-weight:bold;")
            message = detail or "Server unreachable"
            self.warning_label.setText(message)
            self.main_status_bar.showMessage(message)

    def _participants_from_server(self):
        ok, response = send_control_command(self.server_ip, f"LIST:{self.my_id}")
        if not ok:
            return None, response

        participants = _parse_client_list_response(response)
        if self.my_id not in participants:
            participants.append(self.my_id)

        participants = sorted(set(participants), key=_sort_client_ids)
        return participants, ""

    def refresh_participants(self, silent=False):
        participants, error = self._participants_from_server()
        if participants is None:
            if not silent:
                self._set_connected_state(False, f"Refresh failed: {error}")
            return

        if not silent:
            self._set_connected_state(True, "Participant list refreshed")
        elif not self.connected:
            self._set_connected_state(True, "Connection restored")

        self.targets.intersection_update(participants)
        self.muted_participants.intersection_update(participants)

        self.participant_rows.clear()
        self.participant_list.clear()

        for cid in participants:
            row = ParticipantRow(
                client_id=cid,
                is_self=(cid == self.my_id),
                talk_checked=(cid in self.targets),
                mute_checked=(cid in self.muted_participants),
                talk_cb=self.on_talk_toggled,
                mute_cb=self.on_mute_toggled,
                parent=self.participant_list,
            )
            item = QListWidgetItem()
            item.setSizeHint(row.widget.sizeHint())
            self.participant_list.addItem(item)
            self.participant_list.setItemWidget(item, row.widget)
            self.participant_rows[cid] = row

        self._recompute_hear_targets()
        self.update_hear_targets()
        self._sync_broadcast_button()
        self.apply_search_filter()

    def apply_search_filter(self):
        query = self.search_input.text().strip().lower()
        shown = 0
        total = self.participant_list.count()

        for i in range(total):
            item = self.participant_list.item(i)
            widget = self.participant_list.itemWidget(item)
            name_label = widget.findChild(QLabel, "participantName") if widget else None
            text = name_label.text().lower() if name_label else ""
            visible = (query in text) if query else True
            item.setHidden(not visible)
            if visible:
                shown += 1

        self.count_label.setText(f"{shown} / {total} shown")

    def _recompute_hear_targets(self):
        participants = set(self.participant_rows.keys())
        self.hear_targets = {cid for cid in participants if cid != self.my_id and cid not in self.muted_participants}

    def update_hear_targets(self):
        hear = ",".join(sorted(self.hear_targets, key=_sort_client_ids))
        ok, response = send_control_command(self.server_ip, f"HEAR:{self.my_id}:{hear}")
        if ok and response == "OK":
            self._set_connected_state(True)
        else:
            self._set_connected_state(False, f"Failed to update hear targets: {response}")

    def on_talk_toggled(self, client_id, enabled):
        if client_id == self.my_id:
            return

        if enabled:
            self.targets.add(client_id)
        else:
            self.targets.discard(client_id)

        self.update_targets()

    def on_mute_toggled(self, client_id, enabled):
        if client_id == self.my_id:
            return

        if enabled:
            self.muted_participants.add(client_id)
        else:
            self.muted_participants.discard(client_id)

        self._recompute_hear_targets()
        self.update_hear_targets()

    def update_targets(self):
        if self.targets and not self.audio.running:
            if self._stop_capture_timer.isActive():
                self._stop_capture_timer.stop()
            self.audio.start(self.server_ip)
        elif not self.targets and self.audio.running and not self._stop_capture_timer.isActive():
            self._stop_capture_timer.start()

        targets = ",".join(sorted(self.targets, key=_sort_client_ids))
        ok, response = send_control_command(self.server_ip, f"TARGETS:{self.my_id}:{targets}")
        if ok and response == "OK":
            self._set_connected_state(True)
        else:
            self._set_connected_state(False, f"Failed to update targets: {response}")
        self._sync_broadcast_button()

    def _stop_capture_if_idle(self):
        if not self.targets and self.audio.running:
            self.audio.stop()

    def _all_other_clients(self):
        return {cid for cid in self.participant_rows.keys() if cid != self.my_id}

    def _sync_broadcast_button(self):
        all_targets = self._all_other_clients()
        is_broadcast = bool(all_targets) and self.targets == all_targets
        self.broadcast_button.blockSignals(True)
        self.broadcast_button.setChecked(is_broadcast)
        self.broadcast_button.setText("Broadcast On" if is_broadcast else "Broadcast Off")
        self.broadcast_button.blockSignals(False)

    def toggle_broadcast(self, enabled):
        all_targets = self._all_other_clients()
        if enabled and not all_targets:
            self.broadcast_button.blockSignals(True)
            self.broadcast_button.setChecked(False)
            self.broadcast_button.setText("Broadcast Off")
            self.broadcast_button.blockSignals(False)
            return

        self.targets = set(all_targets) if enabled else set()
        for cid, row in self.participant_rows.items():
            if cid == self.my_id:
                continue
            row.set_talk_checked(cid in self.targets)
        self.update_targets()

    def toggle_self_mute(self, muted):
        self.audio.set_tx_muted(muted)
        if muted:
            self.mute_button.setText("Unmute Mic")
            self.main_status_bar.showMessage("Microphone muted")
        else:
            self.mute_button.setText("Mute Mic")
            self.main_status_bar.showMessage("Microphone unmuted")
        self_row = self.participant_rows.get(self.my_id)
        if self_row is not None:
            self_row.set_mic_status(not muted)

    def open_settings(self):
        dlg = SettingsDialog(self.audio, self.server_ip, self.reconnect_to_server, self)
        dlg.exec()

    def reconnect_to_server(self):
        ok, response = send_control_command(self.server_ip, f"PING:{self.my_id}", timeout=3.0)
        if ok and response == "OK":
            self._set_connected_state(True, "Connection healthy")
            self.refresh_participants()
            return True, "Connection already active."

        reg_ok, reg_resp = send_control_command(
            self.server_ip,
            f"REGISTER:{self.my_id}:{self.audio.port}:{REGISTER_SECRET}",
            timeout=5.0,
        )
        if not reg_ok:
            self._set_connected_state(False, f"Reconnect failed: {reg_resp}")
            return False, reg_resp

        if reg_resp not in ("OK", "TAKEN"):
            self._set_connected_state(False, f"Reconnect failed: {reg_resp}")
            return False, reg_resp

        join_ok, multicast_addr, join_resp = join_room(self.server_ip, self.my_id, DEFAULT_ROOM)
        if not join_ok:
            self._set_connected_state(False, f"Join failed: {join_resp}")
            return False, join_resp

        self._set_connected_state(True, "Reconnected to server")
        self.refresh_participants()
        return True, "Re-registered and joined room main."

    def heartbeat_loop(self):
        while not self._hb_stop.is_set():
            ok, response = send_control_command(self.server_ip, f"PING:{self.my_id}", timeout=3.0)
            alive = ok and response == "OK"
            self.heartbeat_result.emit(alive)
            self._hb_stop.wait(8.0)

    def _handle_heartbeat(self, alive):
        if self._cleaned_up:
            return

        if alive:
            self._heartbeat_failures = 0
            if not self.connected:
                self._set_connected_state(True, "Connection restored")
        else:
            self._heartbeat_failures += 1
            if self._heartbeat_failures >= 2:
                self._set_connected_state(False, "Disconnected from server")

    def update_live_ui(self):
        mic_level = self.audio.capture_level
        self.system_level_bar.setValue(mic_level)
        self.volume_controls.set_mic_level(mic_level)

        speaking_state = {}
        # Track self speaking state so active speaker UI can show all current speakers.
        self_speaking = bool(self.audio.capture_active and not self.audio.tx_muted)
        self_row = self.participant_rows.get(self.my_id)
        if self_row is not None:
            self_row.set_volume(mic_level)
            self_row.set_mic_status(not self.audio.tx_muted)
        speaking_state[self.my_id] = self_speaking

        prev_self_active = self.speaker_state.get(self.my_id, False)
        if self_speaking and not prev_self_active:
            timestamp = time.strftime("%H:%M:%S")
            self.speaker_log_list.addItem(f"[{timestamp}] Client {self.my_id} speaking")
        elif prev_self_active and not self_speaking:
            timestamp = time.strftime("%H:%M:%S")
            self.speaker_log_list.addItem(f"[{timestamp}] Client {self.my_id} stopped")
        self.speaker_state[self.my_id] = self_speaking

        for cid, row in self.participant_rows.items():
            if cid == self.my_id:
                continue
            raw_level = float(self.audio.stream_levels.get("__mixed__", 0.0))
            level = min(100, int((raw_level * 100) / 32767))
            is_active = level >= 2

            row.set_volume(level)
            row.set_mic_status(is_active)

            was_active = self.speaker_state.get(cid, False)
            if is_active and not was_active:
                timestamp = time.strftime("%H:%M:%S")
                self.speaker_log_list.addItem(f"[{timestamp}] Client {cid} speaking")
            elif was_active and not is_active:
                timestamp = time.strftime("%H:%M:%S")
                self.speaker_log_list.addItem(f"[{timestamp}] Client {cid} stopped")
            while self.speaker_log_list.count() > 200:
                self.speaker_log_list.takeItem(0)

            self.speaker_state[cid] = is_active
            speaking_state[cid] = is_active

        status_lines = []
        for cid in sorted(self.participant_rows.keys(), key=_sort_client_ids):
            state = "talking" if speaking_state.get(cid, False) else "listening"
            status_lines.append(f"Client {cid} - {state}")

        self.active_speakers_label.setText("\n".join(status_lines) if status_lines else "No clients")

    def leave_room_and_exit(self):
        self._cleanup(unregister=True)
        self.close()

    def _cleanup(self, unregister=True):
        if self._cleaned_up:
            return

        self._cleaned_up = True
        self._hb_stop.set()

        if self._ui_timer.isActive():
            self._ui_timer.stop()

        if self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.stop()

        if self._stop_capture_timer.isActive():
            self._stop_capture_timer.stop()

        if unregister and not self._unregistered:
            try:
                send_control_command(self.server_ip, f"UNREGISTER:{self.my_id}")
            except Exception:
                pass
            self._unregistered = True

        self.audio.shutdown()

    def closeEvent(self, event):
        self._cleanup(unregister=True)
        event.accept()


def register_client_with_server(client_id, server_ip, audio_port):
    try:
        ok, response = send_control_command(
            server_ip,
            f"REGISTER:{client_id}:{audio_port}:{REGISTER_SECRET}",
        )
        if not ok:
            print(f"[CLIENT] Registration error: {response}")
            return False, None

        if response == "TAKEN":
            print(f"[CLIENT] Client ID {client_id} already taken")
            return False, None

        if response != "OK":
            print(f"[CLIENT] Unexpected registration response: {response}")
            return False, None

        join_ok, multicast_addr, join_response = join_room(server_ip, client_id, DEFAULT_ROOM)
        if not join_ok:
            print(f"[CLIENT] JOIN failed for client {client_id}: {join_response}")
            return False, None

        print(f"[CLIENT] Registration successful for client {client_id}")
        return True, multicast_addr
    except Exception as e:
        print(f"[CLIENT] Registration error: {e}")
        return False, None


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

    app = QApplication(sys.argv)
    if os.path.exists(APP_ICON_PATH):
        app.setWindowIcon(QIcon(APP_ICON_PATH))

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
    registered, multicast_addr = register_client_with_server(client_id, net.server_ip, audio_port)
    if not registered:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Registration Failed")
        msg.setText(f"Client ID {client_id} is already in use or registration failed!")
        msg.setInformativeText("Please choose a different client ID and try again.")
        msg.exec()
        audio.shutdown()
        sys.exit(1)

    print("[CLIENT] Registration successful - starting UI...")

    try:
        w = MainWindow(client_id, net.server_ip, audio)
        w.show()
        print("[CLIENT] Client ready")
        sys.exit(app.exec())
    except Exception as e:
        print(f"[CLIENT] Failed to start main window: {e}")
        audio.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
