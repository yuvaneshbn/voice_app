#ifndef CLIENT_ECHO_CANCELLER_H
#define CLIENT_ECHO_CANCELLER_H

#include <cstdint>
#include <vector>

bool echo_cancel_available();

class EchoCanceller {
public:
    EchoCanceller(int sample_rate, int channels, int frame_size, int delay_ms = 60);
    ~EchoCanceller();

    EchoCanceller(const EchoCanceller&) = delete;
    EchoCanceller& operator=(const EchoCanceller&) = delete;

    bool setDelayMs(int delay_ms);
    bool processReverse(const int16_t* frame, int frame_samples);
    std::vector<int16_t> processCapture(const int16_t* frame, int frame_samples);

private:
    int frame_size_ = 0;
    void* handle_ = nullptr;
};

#endif
