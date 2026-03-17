#include "volume_control_panel.h"

#include "audio_engine.h"
#include "ui_helpers.h"

#include <QCheckBox>
#include <QLabel>
#include <QProgressBar>
#include <QPushButton>
#include <QSlider>
#include <QWidget>

VolumeControlPanel::VolumeControlPanel(AudioEngine* audio, QObject* parent)
    : QObject(parent), audio_(audio) {
    widget_ = load_ui_widget(ui_path("volume_control.ui"), nullptr);

    master_slider_ = require_child<QSlider>(widget_, "masterSlider");
    gain_slider_ = require_child<QSlider>(widget_, "gainSlider");
    noise_suppression_label_ = require_child<QLabel>(widget_, "noiseSuppressionLabel");
    noise_suppression_slider_ = require_child<QSlider>(widget_, "noiseSuppressionSlider");
    noise_suppression_enable_checkbox_ = require_child<QCheckBox>(widget_, "noiseSuppressionEnableCheckbox");
    echo_checkbox_ = require_child<QCheckBox>(widget_, "echoCheckbox");
    test_mic_button_ = require_child<QPushButton>(widget_, "testMicButton");
    test_status_label_ = require_child<QLabel>(widget_, "testStatusLabel");
    mic_level_bar_ = require_child<QProgressBar>(widget_, "micLevelBar");

    configureControls();
    wireSignals();
}

void VolumeControlPanel::configureControls() {
    master_slider_->setMinimum(0);
    master_slider_->setMaximum(100);
    master_slider_->setValue(100);

    gain_slider_->setMinimum(-20);
    gain_slider_->setMaximum(20);
    gain_slider_->setValue(0);

    noise_suppression_slider_->setMinimum(0);
    noise_suppression_slider_->setMaximum(100);
    noise_suppression_slider_->setValue(0);
    noise_suppression_enable_checkbox_->setChecked(false);
    echo_checkbox_->setChecked(audio_ ? audio_->echoEnabled() : false);
    echo_checkbox_->setEnabled(audio_ ? audio_->echoAvailable() : false);
    syncNoiseSuppressionControls();

    mic_level_bar_->setMinimum(0);
    mic_level_bar_->setMaximum(100);
    mic_level_bar_->setValue(0);
    test_status_label_->setText("Ready");
}

void VolumeControlPanel::wireSignals() {
    connect(master_slider_, &QSlider::valueChanged, this, [this](int value) {
        if (audio_) {
            audio_->setMasterVolume(value);
        }
    });
    connect(gain_slider_, &QSlider::valueChanged, this, [this](int value) {
        if (audio_) {
            audio_->setGainDb(value);
        }
    });
    connect(noise_suppression_slider_, &QSlider::valueChanged, this, [this](int value) {
        if (audio_) {
            audio_->setNoiseSuppression(value);
        }
    });
    connect(noise_suppression_enable_checkbox_, &QCheckBox::toggled, this, &VolumeControlPanel::onNoiseSuppressionToggled);
    connect(echo_checkbox_, &QCheckBox::toggled, this, [this](bool enabled) {
        if (audio_) {
            audio_->setEchoEnabled(enabled);
        }
    });
    connect(test_mic_button_, &QPushButton::clicked, this, &VolumeControlPanel::testMicrophone);
}

void VolumeControlPanel::syncNoiseSuppressionControls() {
    const bool enabled = noise_suppression_enable_checkbox_->isChecked();
    noise_suppression_slider_->setEnabled(enabled);
    noise_suppression_label_->setEnabled(enabled);
}

void VolumeControlPanel::onNoiseSuppressionToggled(bool checked) {
    if (audio_) {
        audio_->setNoiseSuppressionEnabled(checked);
    }
    syncNoiseSuppressionControls();
}

void VolumeControlPanel::testMicrophone() {
    if (!audio_) {
        return;
    }
    const int level = audio_->testMicrophoneLevel(0.8);
    setMicLevel(level);
    test_status_label_->setText(QString("Mic level: %1%").arg(level));
}

void VolumeControlPanel::setMicLevel(int level) {
    mic_level_bar_->setValue(std::max(0, std::min(100, level)));
}
