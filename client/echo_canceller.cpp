#include "echo_canceller.h"

#include <algorithm>
#include <cstring>
#include <stdexcept>

#if VOICE_HAVE_NATIVE_MIXER
extern "C" {
void* ec_create(int sampleRate, int channels, int frameSize);
void ec_destroy(void* handle);
int ec_set_delay_ms(void* handle, int delayMs);
int ec_process_reverse(void* handle, const int16_t* farFrame, int frameSamples);
int ec_process_capture(void* handle, const int16_t* nearFrame, int frameSamples, int16_t* outFrame);
int ec_get_metrics(void* handle, float* erl, float* erle, int* delayMs);
}
#endif

bool echo_cancel_available() {
#if VOICE_HAVE_NATIVE_MIXER
    return true;
#else
    return false;
#endif
}

EchoCanceller::EchoCanceller(int sample_rate, int channels, int frame_size, int delay_ms)
    : frame_size_(frame_size) {
#if VOICE_HAVE_NATIVE_MIXER
    handle_ = ec_create(sample_rate, channels, frame_size);
    if (!handle_) {
        throw std::runtime_error("Failed to create native echo canceller");
    }
    ec_set_delay_ms(handle_, delay_ms);
#else
    (void)sample_rate;
    (void)channels;
    (void)delay_ms;
    throw std::runtime_error("Native echo cancel API is not available in native_mixer.dll");
#endif
}

EchoCanceller::~EchoCanceller() {
#if VOICE_HAVE_NATIVE_MIXER
    if (handle_) {
        ec_destroy(handle_);
        handle_ = nullptr;
    }
#endif
}

bool EchoCanceller::setDelayMs(int delay_ms) {
#if VOICE_HAVE_NATIVE_MIXER
    if (!handle_) {
        return false;
    }
    return ec_set_delay_ms(handle_, delay_ms) != 0;
#else
    (void)delay_ms;
    return false;
#endif
}

bool EchoCanceller::processReverse(const int16_t* frame, int frame_samples) {
#if VOICE_HAVE_NATIVE_MIXER
    if (!handle_ || !frame || frame_samples <= 0) {
        return false;
    }
    std::vector<int16_t> padded(static_cast<size_t>(frame_size_), 0);
    const int copy_count = std::min(frame_size_, frame_samples);
    std::memcpy(padded.data(), frame, static_cast<size_t>(copy_count) * sizeof(int16_t));
    return ec_process_reverse(handle_, padded.data(), frame_size_) != 0;
#else
    (void)frame;
    (void)frame_samples;
    return false;
#endif
}

std::vector<int16_t> EchoCanceller::processCapture(const int16_t* frame, int frame_samples) {
    if (!frame || frame_samples <= 0) {
        return {};
    }

    std::vector<int16_t> padded(static_cast<size_t>(frame_size_), 0);
    const int copy_count = std::min(frame_size_, frame_samples);
    std::memcpy(padded.data(), frame, static_cast<size_t>(copy_count) * sizeof(int16_t));

#if VOICE_HAVE_NATIVE_MIXER
    if (!handle_) {
        return padded;
    }
    std::vector<int16_t> out(static_cast<size_t>(frame_size_), 0);
    if (ec_process_capture(handle_, padded.data(), frame_size_, out.data()) == 0) {
        return padded;
    }
    return out;
#else
    return padded;
#endif
}
