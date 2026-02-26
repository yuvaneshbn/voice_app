#include "agc.h"

#include <algorithm>
#include <cmath>

SimpleAGC::SimpleAGC(float targetRMS) : targetRMS(targetRMS), gain(1.0f) {}

float SimpleAGC::process(const int16_t* samples, int frameSize) {
    if (!samples || frameSize <= 0) {
        return gain;
    }
    double energy = 0.0;
    for (int i = 0; i < frameSize; ++i) {
        const double s = static_cast<double>(samples[i]);
        energy += s * s;
    }
    float rms = static_cast<float>(std::sqrt(energy / frameSize));
    if (rms < 1.0f) {
        rms = 1.0f;
    }
    const float desired = targetRMS / rms;
    const float attack = 0.2f;   // Keep fast attack
    const float release = 0.15f; // ← FIXED: 15% per frame (faster decay, ~133ms to settle)
    if (desired > gain) {
        gain += (desired - gain) * attack;
    } else {
        gain += (desired - gain) * release;
    }
    gain = std::clamp(gain, 0.3f, 2.5f); // ← FIXED: Lower max gain (less over-amplification)
    return this->gain;
}
