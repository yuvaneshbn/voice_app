#ifndef VOICE_SERVER_H
#define VOICE_SERVER_H

#include <atomic>
#include <memory>
#include <string>

class VoiceServer {
public:
    VoiceServer();
    ~VoiceServer();

    bool start();
    void stop();
    bool isRunning() const { return running_.load(); }

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
    std::atomic<bool> running_{false};
};

#endif
