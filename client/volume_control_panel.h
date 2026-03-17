#ifndef VOLUME_CONTROL_PANEL_H
#define VOLUME_CONTROL_PANEL_H

#include <QObject>

class QWidget;
class QLabel;
class QCheckBox;
class QProgressBar;
class QPushButton;
class QSlider;

class AudioEngine;

class VolumeControlPanel : public QObject {
    Q_OBJECT

public:
    explicit VolumeControlPanel(AudioEngine* audio, QObject* parent = nullptr);

    QWidget* widget() const { return widget_; }
    void setMicLevel(int level);

private:
    void configureControls();
    void wireSignals();
    void syncNoiseSuppressionControls();

    void onNoiseSuppressionToggled(bool checked);
    void testMicrophone();

    AudioEngine* audio_ = nullptr;
    QWidget* widget_ = nullptr;

    QSlider* master_slider_ = nullptr;
    QSlider* gain_slider_ = nullptr;
    QLabel* noise_suppression_label_ = nullptr;
    QSlider* noise_suppression_slider_ = nullptr;
    QCheckBox* noise_suppression_enable_checkbox_ = nullptr;
    QCheckBox* echo_checkbox_ = nullptr;
    QPushButton* test_mic_button_ = nullptr;
    QLabel* test_status_label_ = nullptr;
    QProgressBar* mic_level_bar_ = nullptr;
};

#endif
