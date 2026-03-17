#ifndef PARTICIPANT_ROW_H
#define PARTICIPANT_ROW_H

#include <QObject>
#include <QString>

class QWidget;
class QLabel;
class QCheckBox;
class QProgressBar;

class ParticipantRow : public QObject {
    Q_OBJECT

public:
    ParticipantRow(const QString& client_id,
                   bool is_self,
                   bool talk_checked,
                   bool mute_checked,
                   QObject* parent = nullptr);

    QWidget* widget() const { return widget_; }
    const QString& clientId() const { return client_id_; }

    void setTalkChecked(bool enabled);
    void setMuteChecked(bool enabled);
    void setVolume(int value);
    void setMicStatus(bool is_on);

signals:
    void talkToggled(const QString& client_id, bool enabled);
    void muteToggled(const QString& client_id, bool enabled);

private:
    QString client_id_;
    bool is_self_ = false;
    QWidget* widget_ = nullptr;
    QLabel* name_label_ = nullptr;
    QCheckBox* talk_checkbox_ = nullptr;
    QCheckBox* mute_checkbox_ = nullptr;
    QLabel* mic_status_label_ = nullptr;
    QProgressBar* volume_bar_ = nullptr;
};

#endif
