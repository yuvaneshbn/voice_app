#include "main_window.h"

#include "audio_engine.h"
#include "control_client.h"
#include "participant_row.h"
#include "settings_dialog.h"
#include "ui_helpers.h"
#include "volume_control_panel.h"

#include <QCheckBox>
#include <QCloseEvent>
#include <QComboBox>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QListWidgetItem>
#include <QMessageBox>
#include <QProgressBar>
#include <QPushButton>
#include <QStatusBar>
#include <QTime>
#include <QVBoxLayout>

#include <algorithm>
#include <chrono>
#include <iostream>

namespace {
constexpr const char* DEFAULT_ROOM_NAME = "main";

std::pair<int, QString> sortClientKey(const QString& client_id) {
    bool ok = false;
    int number = client_id.toInt(&ok);
    if (ok) {
        return {0, QString::number(number)};
    }
    return {1, client_id};
}

bool join_room(const std::string& server_ip, const std::string& client_id, std::string& multicast_out) {
    const auto resp = send_control_command(server_ip, "JOIN:" + client_id + ":" + DEFAULT_ROOM_NAME);
    if (!resp.ok) {
        return false;
    }
    if (resp.response.rfind("OK", 0) != 0) {
        return false;
    }
    const auto colon = resp.response.find(':');
    if (colon != std::string::npos) {
        multicast_out = resp.response.substr(colon + 1);
    }
    return true;
}

} // namespace

MainWindow::MainWindow(const QString& my_id,
                       const QString& server_ip,
                       AudioEngine* audio,
                       const QString& register_secret,
                       QWidget* parent)
    : QMainWindow(parent),
      my_id_(my_id),
      server_ip_(server_ip),
      register_secret_(register_secret),
      audio_(audio) {
    audio_->setClientId(my_id_.toStdString());

    root_ = load_ui_widget(ui_path("main_window.ui"), this);
    setCentralWidget(root_);
    setWindowTitle(root_->windowTitle());

    room_combo_ = require_child<QComboBox>(root_, "roomCombo");
    join_leave_button_ = require_child<QPushButton>(root_, "joinLeaveButton");
    refresh_button_ = require_child<QPushButton>(root_, "refreshButton");
    connection_indicator_ = require_child<QLabel>(root_, "connectionIndicator");

    search_input_ = require_child<QLineEdit>(root_, "searchInput");
    participant_list_ = require_child<QListWidget>(root_, "participantList");
    count_label_ = require_child<QLabel>(root_, "countLabel");

    active_speakers_label_ = require_child<QLabel>(root_, "activeSpeakersLabel");
    speaker_log_list_ = require_child<QListWidget>(root_, "speakerLogList");
    system_level_bar_ = require_child<QProgressBar>(root_, "systemLevelBar");

    controls_layout_ = root_->findChild<QVBoxLayout*>("controlsPlaceholderLayout");
    if (!controls_layout_) {
        QWidget* group = root_->findChild<QWidget*>("myControlsGroup");
        if (group) {
            controls_layout_ = qobject_cast<QVBoxLayout*>(group->layout());
        }
    }
    if (!controls_layout_) {
        throw std::runtime_error("Missing required controls layout");
    }
    controls_hint_ = root_->findChild<QLabel*>("controlsHint");

    mute_button_ = require_child<QPushButton>(root_, "muteButton");
    broadcast_button_ = require_child<QPushButton>(root_, "broadcastButton");
    settings_button_ = require_child<QPushButton>(root_, "settingsButton");

    warning_label_ = require_child<QLabel>(root_, "warningLabel");
    main_status_bar_ = require_child<QStatusBar>(root_, "mainStatusBar");

    volume_controls_ = new VolumeControlPanel(audio_, this);
    if (controls_hint_) {
        controls_hint_->setParent(nullptr);
    }
    controls_layout_->addWidget(volume_controls_->widget());

    room_combo_->clear();
    room_combo_->addItem(DEFAULT_ROOM_NAME);
    room_combo_->setCurrentText(DEFAULT_ROOM_NAME);
    room_combo_->setEnabled(false);

    join_leave_button_->setText("Leave Room");
    connect(join_leave_button_, &QPushButton::clicked, this, [this]() { cleanup(true); close(); });

    connect(refresh_button_, &QPushButton::clicked, this, [this]() { refreshParticipants(false); });
    connect(search_input_, &QLineEdit::textChanged, this, [this]() { applySearchFilter(); });

    mute_button_->setCheckable(true);
    connect(mute_button_, &QPushButton::toggled, this, &MainWindow::toggleSelfMute);

    broadcast_button_->setCheckable(true);
    broadcast_button_->setChecked(false);
    broadcast_button_->setText("Broadcast Off");
    connect(broadcast_button_, &QPushButton::toggled, this, &MainWindow::toggleBroadcast);

    connect(settings_button_, &QPushButton::clicked, this, [this]() {
        SettingsDialog dlg(audio_, server_ip_, [this]() {
            QString message;
            bool ok = reconnectToServer(message);
            return std::make_pair(ok, message);
        }, this);
        dlg.exec();
    });

    stop_capture_timer_.setSingleShot(true);
    stop_capture_timer_.setInterval(1200);
    connect(&stop_capture_timer_, &QTimer::timeout, this, &MainWindow::stopCaptureIfIdle);

    ui_timer_.setInterval(200);
    connect(&ui_timer_, &QTimer::timeout, this, &MainWindow::updateLiveUi);
    ui_timer_.start();

    auto_refresh_timer_.setInterval(3000);
    connect(&auto_refresh_timer_, &QTimer::timeout, this, [this]() { refreshParticipants(true); });
    auto_refresh_timer_.start();

    system_level_bar_->setMinimum(0);
    system_level_bar_->setMaximum(100);
    system_level_bar_->setValue(0);

    main_status_bar_->showMessage(QString("Client %1 connected to %2").arg(my_id_, server_ip_));
    setConnectedState(true);

    refreshParticipants(false);
    heartbeat_thread_ = std::thread(&MainWindow::heartbeatLoop, this);
}

MainWindow::~MainWindow() {
    cleanup(true);
}

void MainWindow::setConnectedState(bool connected, const QString& detail) {
    connected_ = connected;
    if (connected_) {
        connection_indicator_->setText("Connected");
        connection_indicator_->setStyleSheet("color:#1E8E3E; font-weight:bold;");
        if (!detail.isEmpty()) {
            main_status_bar_->showMessage(detail);
        }
        warning_label_->setText("");
    } else {
        connection_indicator_->setText("Disconnected");
        connection_indicator_->setStyleSheet("color:#C62828; font-weight:bold;");
        QString message = detail.isEmpty() ? "Server unreachable" : detail;
        warning_label_->setText(message);
        main_status_bar_->showMessage(message);
    }
}

void MainWindow::refreshParticipants(bool silent) {
    const auto resp = send_control_command(server_ip_.toStdString(), "LIST:" + my_id_.toStdString());
    if (!resp.ok) {
        if (!silent) {
            setConnectedState(false, QString("Refresh failed: %1").arg(QString::fromStdString(resp.response)));
        }
        return;
    }

    std::vector<std::string> participants = parse_client_list_response(resp.response);
    bool has_self = false;
    for (const auto& p : participants) {
        if (p == my_id_.toStdString()) {
            has_self = true;
            break;
        }
    }
    if (!has_self) {
        participants.push_back(my_id_.toStdString());
    }

    std::sort(participants.begin(), participants.end(), [](const std::string& a, const std::string& b) {
        QString qa = QString::fromStdString(a);
        QString qb = QString::fromStdString(b);
        return sortClientKey(qa) < sortClientKey(qb);
    });
    participants.erase(std::unique(participants.begin(), participants.end()), participants.end());

    if (!silent) {
        setConnectedState(true, "Participant list refreshed");
    } else if (!connected_) {
        setConnectedState(true, "Connection restored");
    }

    QSet<QString> participant_set;
    for (const auto& p : participants) {
        participant_set.insert(QString::fromStdString(p));
    }

    targets_ = targets_.intersect(participant_set);
    muted_participants_ = muted_participants_.intersect(participant_set);

    for (auto it = participant_rows_.begin(); it != participant_rows_.end(); ++it) {
        delete it.value();
    }
    participant_rows_.clear();
    participant_list_->clear();

    for (const auto& p : participants) {
        const QString cid = QString::fromStdString(p);
        bool is_self = (cid == my_id_);
        auto* row = new ParticipantRow(cid, is_self, targets_.contains(cid), muted_participants_.contains(cid), this);
        connect(row, &ParticipantRow::talkToggled, this, [this](const QString& client_id, bool enabled) {
            if (client_id == my_id_) {
                return;
            }
            if (enabled) {
                targets_.insert(client_id);
            } else {
                targets_.remove(client_id);
            }
            updateTargets();
        });
        connect(row, &ParticipantRow::muteToggled, this, [this](const QString& client_id, bool enabled) {
            if (client_id == my_id_) {
                return;
            }
            if (enabled) {
                muted_participants_.insert(client_id);
            } else {
                muted_participants_.remove(client_id);
            }
            recomputeHearTargets();
            updateHearTargets();
        });

        auto* item = new QListWidgetItem();
        item->setSizeHint(row->widget()->sizeHint());
        participant_list_->addItem(item);
        participant_list_->setItemWidget(item, row->widget());
        participant_rows_.insert(cid, row);
    }

    recomputeHearTargets();
    updateHearTargets();
    syncBroadcastButton();
    applySearchFilter();
}

void MainWindow::applySearchFilter() {
    const QString query = search_input_->text().trimmed().toLower();
    int shown = 0;
    const int total = participant_list_->count();

    for (int i = 0; i < total; ++i) {
        auto* item = participant_list_->item(i);
        QWidget* widget = participant_list_->itemWidget(item);
        auto* name_label = widget ? widget->findChild<QLabel*>("participantName") : nullptr;
        const QString text = name_label ? name_label->text().toLower() : QString();
        const bool visible = query.isEmpty() ? true : text.contains(query);
        item->setHidden(!visible);
        if (visible) {
            ++shown;
        }
    }

    count_label_->setText(QString("%1 / %2 shown").arg(shown).arg(total));
}

void MainWindow::recomputeHearTargets() {
    hear_targets_.clear();
    for (auto it = participant_rows_.begin(); it != participant_rows_.end(); ++it) {
        const QString cid = it.key();
        if (cid != my_id_ && !muted_participants_.contains(cid)) {
            hear_targets_.insert(cid);
        }
    }
}

void MainWindow::updateHearTargets() {
    QStringList hear_list = hear_targets_.values();
    std::sort(hear_list.begin(), hear_list.end(), [](const QString& a, const QString& b) {
        return sortClientKey(a) < sortClientKey(b);
    });
    const QString payload = hear_list.join(",");
    const auto resp = send_control_command(server_ip_.toStdString(), "HEAR:" + my_id_.toStdString() + ":" + payload.toStdString());
    if (resp.ok && resp.response == "OK") {
        setConnectedState(true);
    } else {
        setConnectedState(false, QString("Failed to update hear targets: %1").arg(QString::fromStdString(resp.response)));
    }
}

void MainWindow::updateTargets() {
    if (!targets_.isEmpty() && !audio_->isRunning()) {
        if (stop_capture_timer_.isActive()) {
            stop_capture_timer_.stop();
        }
        audio_->start(server_ip_.toStdString());
    } else if (targets_.isEmpty() && audio_->isRunning() && !stop_capture_timer_.isActive()) {
        stop_capture_timer_.start();
    }

    QStringList target_list = targets_.values();
    std::sort(target_list.begin(), target_list.end(), [](const QString& a, const QString& b) {
        return sortClientKey(a) < sortClientKey(b);
    });
    const QString payload = target_list.join(",");
    const auto resp = send_control_command(server_ip_.toStdString(), "TARGETS:" + my_id_.toStdString() + ":" + payload.toStdString());
    if (resp.ok && resp.response == "OK") {
        setConnectedState(true);
    } else {
        setConnectedState(false, QString("Failed to update targets: %1").arg(QString::fromStdString(resp.response)));
    }
    syncBroadcastButton();
}

void MainWindow::stopCaptureIfIdle() {
    if (targets_.isEmpty() && audio_->isRunning()) {
        audio_->stop();
    }
}

void MainWindow::syncBroadcastButton() {
    QSet<QString> all_targets;
    for (auto it = participant_rows_.begin(); it != participant_rows_.end(); ++it) {
        const QString cid = it.key();
        if (cid != my_id_) {
            all_targets.insert(cid);
        }
    }
    const bool is_broadcast = !all_targets.isEmpty() && targets_ == all_targets;
    broadcast_button_->blockSignals(true);
    broadcast_button_->setChecked(is_broadcast);
    broadcast_button_->setText(is_broadcast ? "Broadcast On" : "Broadcast Off");
    broadcast_button_->blockSignals(false);
}

void MainWindow::toggleBroadcast(bool enabled) {
    QSet<QString> all_targets;
    for (auto it = participant_rows_.begin(); it != participant_rows_.end(); ++it) {
        const QString cid = it.key();
        if (cid != my_id_) {
            all_targets.insert(cid);
        }
    }
    if (enabled && all_targets.isEmpty()) {
        broadcast_button_->blockSignals(true);
        broadcast_button_->setChecked(false);
        broadcast_button_->setText("Broadcast Off");
        broadcast_button_->blockSignals(false);
        return;
    }

    targets_ = enabled ? all_targets : QSet<QString>();
    for (auto it = participant_rows_.begin(); it != participant_rows_.end(); ++it) {
        if (it.key() == my_id_) {
            continue;
        }
        it.value()->setTalkChecked(targets_.contains(it.key()));
    }
    updateTargets();
}

void MainWindow::toggleSelfMute(bool muted) {
    audio_->setTxMuted(muted);
    if (muted) {
        mute_button_->setText("Unmute Mic");
        main_status_bar_->showMessage("Microphone muted");
    } else {
        mute_button_->setText("Mute Mic");
        main_status_bar_->showMessage("Microphone unmuted");
    }
    auto it = participant_rows_.find(my_id_);
    if (it != participant_rows_.end()) {
        it.value()->setMicStatus(!muted);
    }
}

void MainWindow::updateLiveUi() {
    const int mic_level = audio_->captureLevel();
    system_level_bar_->setValue(mic_level);
    volume_controls_->setMicLevel(mic_level);

    QMap<QString, bool> speaking_state;
    const bool self_speaking = audio_->captureActive() && !audio_->isTxMuted();
    auto it = participant_rows_.find(my_id_);
    if (it != participant_rows_.end()) {
        it.value()->setVolume(mic_level);
        it.value()->setMicStatus(!audio_->isTxMuted());
    }
    speaking_state.insert(my_id_, self_speaking);

    const bool prev_self_active = speaker_state_.value(my_id_, false);
    if (self_speaking && !prev_self_active) {
        const QString timestamp = QTime::currentTime().toString("HH:mm:ss");
        speaker_log_list_->addItem(QString("[%1] Client %2 speaking").arg(timestamp, my_id_));
    } else if (!self_speaking && prev_self_active) {
        const QString timestamp = QTime::currentTime().toString("HH:mm:ss");
        speaker_log_list_->addItem(QString("[%1] Client %2 stopped").arg(timestamp, my_id_));
    }
    speaker_state_.insert(my_id_, self_speaking);

    const float raw_level = audio_->mixedPeak();
    for (auto it2 = participant_rows_.begin(); it2 != participant_rows_.end(); ++it2) {
        const QString cid = it2.key();
        if (cid == my_id_) {
            continue;
        }
        const int level = std::min(100, static_cast<int>((raw_level * 100) / 32767));
        const bool is_active = level >= 2;
        it2.value()->setVolume(level);
        it2.value()->setMicStatus(is_active);

        const bool was_active = speaker_state_.value(cid, false);
        if (is_active && !was_active) {
            const QString timestamp = QTime::currentTime().toString("HH:mm:ss");
            speaker_log_list_->addItem(QString("[%1] Client %2 speaking").arg(timestamp, cid));
        } else if (!is_active && was_active) {
            const QString timestamp = QTime::currentTime().toString("HH:mm:ss");
            speaker_log_list_->addItem(QString("[%1] Client %2 stopped").arg(timestamp, cid));
        }
        while (speaker_log_list_->count() > 200) {
            delete speaker_log_list_->takeItem(0);
        }
        speaker_state_.insert(cid, is_active);
        speaking_state.insert(cid, is_active);
    }

    QStringList status_lines;
    for (auto it3 = participant_rows_.begin(); it3 != participant_rows_.end(); ++it3) {
        const QString cid = it3.key();
        const QString state = speaking_state.value(cid, false) ? "talking" : "listening";
        status_lines << QString("Client %1 - %2").arg(cid, state);
    }
    active_speakers_label_->setText(status_lines.isEmpty() ? "No clients" : status_lines.join("\n"));
}

void MainWindow::handleHeartbeat(bool alive) {
    if (cleaned_up_) {
        return;
    }
    if (alive) {
        heartbeat_failures_ = 0;
        if (!connected_) {
            setConnectedState(true, "Connection restored");
        }
    } else {
        ++heartbeat_failures_;
        if (heartbeat_failures_ >= 2) {
            setConnectedState(false, "Disconnected from server");
        }
    }
}

bool MainWindow::reconnectToServer(QString& message) {
    auto ping = send_control_command(server_ip_.toStdString(), "PING:" + my_id_.toStdString(), 3000);
    if (ping.ok && ping.response == "OK") {
        setConnectedState(true, "Connection healthy");
        refreshParticipants(false);
        message = "Connection already active.";
        return true;
    }

    auto reg = send_control_command(server_ip_.toStdString(), "REGISTER:" + my_id_.toStdString() + ":" + std::to_string(audio_->port()) + ":" + register_secret_.toStdString(), 5000);
    if (!reg.ok) {
        setConnectedState(false, QString("Reconnect failed: %1").arg(QString::fromStdString(reg.response)));
        message = QString::fromStdString(reg.response);
        return false;
    }

    if (reg.response != "OK" && reg.response != "TAKEN") {
        setConnectedState(false, QString("Reconnect failed: %1").arg(QString::fromStdString(reg.response)));
        message = QString::fromStdString(reg.response);
        return false;
    }

    std::string multicast;
    if (!join_room(server_ip_.toStdString(), my_id_.toStdString(), multicast)) {
        setConnectedState(false, "Join failed");
        message = "Join failed";
        return false;
    }

    setConnectedState(true, "Reconnected to server");
    refreshParticipants(false);
    message = "Re-registered and joined room main.";
    return true;
}

void MainWindow::heartbeatLoop() {
    while (!hb_stop_.load()) {
        const auto resp = send_control_command(server_ip_.toStdString(), "PING:" + my_id_.toStdString(), 3000);
        const bool alive = resp.ok && resp.response == "OK";
        QMetaObject::invokeMethod(this, [this, alive]() { handleHeartbeat(alive); }, Qt::QueuedConnection);
        for (int i = 0; i < 80 && !hb_stop_.load(); ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }
}

void MainWindow::cleanup(bool unregister) {
    if (cleaned_up_) {
        return;
    }
    cleaned_up_ = true;
    hb_stop_.store(true);

    if (ui_timer_.isActive()) {
        ui_timer_.stop();
    }
    if (auto_refresh_timer_.isActive()) {
        auto_refresh_timer_.stop();
    }
    if (stop_capture_timer_.isActive()) {
        stop_capture_timer_.stop();
    }

    if (heartbeat_thread_.joinable()) {
        heartbeat_thread_.join();
    }

    if (unregister && !unregistered_) {
        send_control_command(server_ip_.toStdString(), "UNREGISTER:" + my_id_.toStdString());
        unregistered_ = true;
    }

    audio_->shutdown();
}

void MainWindow::closeEvent(QCloseEvent* event) {
    cleanup(true);
    event->accept();
}
