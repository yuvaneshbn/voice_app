#ifndef SETTINGS_DIALOG_H
#define SETTINGS_DIALOG_H

#include <QDialog>

#include <functional>

class AudioEngine;
class QLabel;
class QComboBox;
class QPushButton;
class QVBoxLayout;
class VolumeControlPanel;

class SettingsDialog : public QDialog {
    Q_OBJECT

public:
    SettingsDialog(AudioEngine* audio,
                   const QString& server_ip,
                   std::function<std::pair<bool, QString>()> reconnect_cb,
                   QWidget* parent = nullptr);

private:
    void populateDevices();
    void onInputDeviceChanged();
    void onOutputDeviceChanged();
    void reconnect();
    void saveAndClose();

    AudioEngine* audio_ = nullptr;
    QString server_ip_;
    std::function<std::pair<bool, QString>()> reconnect_cb_;

    QWidget* form_ = nullptr;
    QComboBox* input_device_combo_ = nullptr;
    QComboBox* output_device_combo_ = nullptr;
    QLabel* server_ip_value_ = nullptr;
    QPushButton* reconnect_button_ = nullptr;
    QPushButton* save_close_button_ = nullptr;
    QPushButton* cancel_button_ = nullptr;

    QVBoxLayout* advanced_audio_layout_ = nullptr;
    QLabel* volume_hint_ = nullptr;
    VolumeControlPanel* volume_controls_ = nullptr;

    bool populating_devices_ = false;
};

#endif
