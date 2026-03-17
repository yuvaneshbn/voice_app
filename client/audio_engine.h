#ifndef AUDIO_ENGINE_H
#define AUDIO_ENGINE_H

#include "../shared/opus_codec.h"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <winsock2.h>

struct AudioDeviceInfo {
    int index;
    std::string name;
};

class EchoCanceller;

class AudioEngine {
public:
    AudioEngine();
    ~AudioEngine();

    bool start(const std::string& server_ip);
    void stop();
    void shutdown();

    int port() const { return port_; }
    void setClientId(const std::string& id) { client_id_ = id; }
    const std::string& clientId() const { return client_id_; }

    std::vector<AudioDeviceInfo> listInputDevices() const;
    std::vector<AudioDeviceInfo> listOutputDevices() const;

    bool setInputDevice(int device_index);
    bool setOutputDevice(int device_index);

    void setMasterVolume(int value);
    void setOutputVolume(int value);
    void setGainDb(int value);
    void setMicSensitivity(int value);
    void setNoiseSuppression(int value);
    void setNoiseSuppressionEnabled(bool enabled);
    void setAutoGain(bool enabled);
    void setEchoEnabled(bool enabled);
    void setTxMuted(bool enabled);

    int testMicrophoneLevel(double duration_sec = 1.0);

    void pushCaptureFrame(const int16_t* samples, int sample_count);
    std::vector<int16_t> mixFrame();

    int captureLevel() const { return capture_level_.load(); }
    bool captureActive() const { return capture_active_.load(); }
    float mixedPeak() const { return mixed_peak_.load(); }

    bool isRunning() const { return running_.load(); }
    bool echoAvailable() const { return echo_available_; }
    bool echoEnabled() const { return echo_enabled_.load(); }
    bool isTxMuted() const;

    int inputDeviceIndex() const { return input_device_index_; }
    int outputDeviceIndex() const { return output_device_index_; }

private:
    void listenLoop();
    void handleIncomingPacket(const std::vector<uint8_t>& data);

    void sendLoop();

    bool openOutput();
    void closeOutput();
    bool openInput();
    void closeInput();

    void updateMixedLevel(const std::vector<int16_t>& frame);

    bool popCaptureFrame(std::vector<int16_t>& out);

    bool popRxFrame(std::vector<int16_t>& out);

    int port_ = 0;
    std::string client_id_;
    std::string server_ip_;

    std::atomic<bool> running_{false};
    std::atomic<bool> listen_running_{true};
    std::atomic<bool> playback_running_{false};

    std::thread listen_thread_;
    std::thread send_thread_;
    std::thread playback_thread_;

    int input_device_index_ = -1;
    int output_device_index_ = -1;

    std::atomic<int> capture_level_{0};
    std::atomic<bool> capture_active_{false};
    std::atomic<float> mixed_peak_{0.0f};
    bool echo_available_ = false;

    float master_volume_ = 1.0f;
    float output_volume_ = 1.0f;
    float tx_gain_db_ = 0.0f;
    int mic_sensitivity_ = 50;
    int noise_suppression_ = 0;
    bool noise_suppression_enabled_ = false;
    bool auto_gain_ = false;
    std::atomic<bool> echo_enabled_{false};

    bool tx_muted_ = false;
    mutable std::mutex config_mutex_;

    EchoCanceller* echo_ = nullptr;
    std::mutex echo_mutex_;

    OpusCodec encoder_;
    OpusCodec decoder_;
    std::mutex decoder_mutex_;

    std::deque<std::vector<int16_t>> rx_frames_;
    mutable std::mutex rx_mutex_;

    std::deque<std::vector<int16_t>> capture_frames_;
    std::mutex capture_mutex_;
    std::condition_variable capture_cv_;

    uint16_t seq_ = 0;
    uint32_t timestamp_ = 0;

    SOCKET recv_sock_ = INVALID_SOCKET;
    SOCKET send_sock_ = INVALID_SOCKET;

    void* wave_out_ = nullptr;
    void* wave_in_ = nullptr;
};

#endif
