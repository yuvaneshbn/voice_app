#include "participant_row.h"

#include "ui_helpers.h"

#include <QCheckBox>
#include <QLabel>
#include <QProgressBar>
#include <QWidget>

ParticipantRow::ParticipantRow(const QString& client_id,
                               bool is_self,
                               bool talk_checked,
                               bool mute_checked,
                               QObject* parent)
    : QObject(parent), client_id_(client_id), is_self_(is_self) {
    widget_ = load_ui_widget(ui_path("participant_item.ui"), nullptr);
    name_label_ = require_child<QLabel>(widget_, "participantName");
    talk_checkbox_ = require_child<QCheckBox>(widget_, "talkCheckbox");
    mute_checkbox_ = find_first_child<QCheckBox>(widget_, "muteCheckbox", "hearCheckbox");
    if (!mute_checkbox_) {
        throw std::runtime_error("Missing required participant mute checkbox");
    }
    mic_status_label_ = require_child<QLabel>(widget_, "micStatusLabel");
    volume_bar_ = require_child<QProgressBar>(widget_, "participantVolumeBar");

    QString name_text = QString("Client %1").arg(client_id_);
    if (is_self_) {
        name_text += " (You)";
    }
    name_label_->setText(name_text);

    talk_checkbox_->setChecked(talk_checked);
    talk_checkbox_->setEnabled(!is_self_);

    mute_checkbox_->setText("Mute");
    mute_checkbox_->setChecked(mute_checked);
    mute_checkbox_->setEnabled(!is_self_);

    mic_status_label_->setText("Mic: Off");
    volume_bar_->setMinimum(0);
    volume_bar_->setMaximum(100);
    volume_bar_->setValue(0);

    connect(talk_checkbox_, &QCheckBox::toggled, this, [this](bool checked) {
        emit talkToggled(client_id_, checked);
    });

    connect(mute_checkbox_, &QCheckBox::toggled, this, [this](bool checked) {
        emit muteToggled(client_id_, checked);
    });
}

void ParticipantRow::setTalkChecked(bool enabled) {
    if (talk_checkbox_->isChecked() != enabled) {
        talk_checkbox_->blockSignals(true);
        talk_checkbox_->setChecked(enabled);
        talk_checkbox_->blockSignals(false);
    }
}

void ParticipantRow::setMuteChecked(bool enabled) {
    if (mute_checkbox_->isChecked() != enabled) {
        mute_checkbox_->blockSignals(true);
        mute_checkbox_->setChecked(enabled);
        mute_checkbox_->blockSignals(false);
    }
}

void ParticipantRow::setVolume(int value) {
    volume_bar_->setValue(std::max(0, std::min(100, value)));
}

void ParticipantRow::setMicStatus(bool is_on) {
    mic_status_label_->setText(is_on ? "Mic: On" : "Mic: Off");
}
