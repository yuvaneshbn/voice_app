#include "settings_dialog.h"

#include "audio_engine.h"
#include "ui_helpers.h"
#include "volume_control_panel.h"

#include <QComboBox>
#include <QLabel>
#include <QMessageBox>
#include <QPushButton>
#include <QVBoxLayout>
#include <QWidget>

SettingsDialog::SettingsDialog(AudioEngine* audio,
                               const QString& server_ip,
                               std::function<std::pair<bool, QString>()> reconnect_cb,
                               QWidget* parent)
    : QDialog(parent), audio_(audio), server_ip_(server_ip), reconnect_cb_(std::move(reconnect_cb)) {
    form_ = load_ui_widget(ui_path("settings_dialog.ui"), this);
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->addWidget(form_);

    setWindowTitle(form_->windowTitle());

    input_device_combo_ = require_child<QComboBox>(form_, "inputDeviceCombo");
    output_device_combo_ = require_child<QComboBox>(form_, "outputDeviceCombo");
    server_ip_value_ = require_child<QLabel>(form_, "serverIpValue");
    reconnect_button_ = require_child<QPushButton>(form_, "reconnectButton");
    save_close_button_ = require_child<QPushButton>(form_, "saveCloseButton");
    cancel_button_ = require_child<QPushButton>(form_, "cancelButton");

    advanced_audio_layout_ = form_->findChild<QVBoxLayout*>("advancedAudioLayout");
    if (!advanced_audio_layout_) {
        QWidget* advanced_group = form_->findChild<QWidget*>("advancedAudioGroup");
        if (advanced_group) {
            advanced_audio_layout_ = qobject_cast<QVBoxLayout*>(advanced_group->layout());
        }
    }
    volume_hint_ = form_->findChild<QLabel*>("volumeControlHint");

    volume_controls_ = new VolumeControlPanel(audio_, this);
    if (volume_hint_) {
        volume_hint_->setParent(nullptr);
    }
    if (advanced_audio_layout_) {
        advanced_audio_layout_->addWidget(volume_controls_->widget());
    }

    server_ip_value_->setText(server_ip_);

    populateDevices();

    connect(input_device_combo_, &QComboBox::currentIndexChanged, this, &SettingsDialog::onInputDeviceChanged);
    connect(output_device_combo_, &QComboBox::currentIndexChanged, this, &SettingsDialog::onOutputDeviceChanged);
    connect(reconnect_button_, &QPushButton::clicked, this, &SettingsDialog::reconnect);
    connect(save_close_button_, &QPushButton::clicked, this, &SettingsDialog::saveAndClose);
    connect(cancel_button_, &QPushButton::clicked, this, &SettingsDialog::reject);
}

void SettingsDialog::populateDevices() {
    populating_devices_ = true;
    input_device_combo_->blockSignals(true);
    output_device_combo_->blockSignals(true);

    input_device_combo_->clear();
    output_device_combo_->clear();

    if (audio_) {
        const auto inputs = audio_->listInputDevices();
        for (const auto& entry : inputs) {
            input_device_combo_->addItem(QString::fromStdString(entry.name), entry.index);
        }

        const auto outputs = audio_->listOutputDevices();
        for (const auto& entry : outputs) {
            output_device_combo_->addItem(QString::fromStdString(entry.name), entry.index);
        }

        const int input_index = audio_->inputDeviceIndex();
        const int output_index = audio_->outputDeviceIndex();

        int pos = input_device_combo_->findData(input_index);
        if (pos >= 0) {
            input_device_combo_->setCurrentIndex(pos);
        }

        pos = output_device_combo_->findData(output_index);
        if (pos >= 0) {
            output_device_combo_->setCurrentIndex(pos);
        }
    }

    input_device_combo_->blockSignals(false);
    output_device_combo_->blockSignals(false);
    populating_devices_ = false;
}

void SettingsDialog::onInputDeviceChanged() {
    if (populating_devices_ || !audio_) {
        return;
    }
    QVariant value = input_device_combo_->currentData();
    if (value.isValid()) {
        audio_->setInputDevice(value.toInt());
    }
}

void SettingsDialog::onOutputDeviceChanged() {
    if (populating_devices_ || !audio_) {
        return;
    }
    QVariant value = output_device_combo_->currentData();
    if (value.isValid()) {
        audio_->setOutputDevice(value.toInt());
    }
}

void SettingsDialog::reconnect() {
    if (!reconnect_cb_) {
        return;
    }
    const auto result = reconnect_cb_();

    QMessageBox box(this);
    box.setWindowTitle("Reconnect");
    if (result.first) {
        box.setIcon(QMessageBox::Information);
        box.setText("Reconnected successfully.");
        if (!result.second.isEmpty()) {
            box.setInformativeText(result.second);
        }
    } else {
        box.setIcon(QMessageBox::Warning);
        box.setText("Reconnect failed.");
        if (!result.second.isEmpty()) {
            box.setInformativeText(result.second);
        }
    }
    box.exec();
}

void SettingsDialog::saveAndClose() {
    onInputDeviceChanged();
    onOutputDeviceChanged();
    accept();
}
