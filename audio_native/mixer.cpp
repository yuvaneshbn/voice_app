#include "mixer.h"

namespace {
inline int16_t clamp32(int32_t x) {
    if (x > 32767) {
        return 32767;
    }
    if (x < -32768) {
        return -32768;
    }
    return static_cast<int16_t>(x);
}
}

AudioMixer::AudioMixer(int frameSize) : frameSize(frameSize), accumulator(frameSize, 0) {}

void AudioMixer::reset() {
    std::fill(accumulator.begin(), accumulator.end(), 0);
}

void AudioMixer::addStream(const int16_t* samples, float gain) {
    if (!samples) {
        return;
    }

    for (int i = 0; i < frameSize; ++i) {
        accumulator[i] += static_cast<int32_t>(samples[i] * gain);
    }
}

void AudioMixer::mix(int16_t* output) {
    if (!output) {
        return;
    }

    for (int i = 0; i < frameSize; ++i) {
        output[i] = clamp32(accumulator[i]);
    }
}
