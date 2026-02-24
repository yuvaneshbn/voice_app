#include "agc.h"

#include <algorithm>
#include <cmath>

SimpleAGC::SimpleAGC(float targetPeak) : emaLevel(1000.0f), targetPeak(targetPeak) {}

float SimpleAGC::process(const int16_t* samples, int frameSize) {
    if (!samples || frameSize <= 0) {
        return 1.0f;
    }

    int16_t peak = 0;
    for (int i = 0; i < frameSize; ++i) {
        peak = std::max<int16_t>(peak, static_cast<int16_t>(std::abs(samples[i])));
    }

    emaLevel = 0.9f * emaLevel + 0.1f * static_cast<float>(peak);
    if (emaLevel < 1.0f) {
        emaLevel = 1.0f;
    }

    float gain = targetPeak / emaLevel;
    if (gain < 0.25f) {
        gain = 0.25f;
    } else if (gain > 4.0f) {
        gain = 4.0f;
    }
    return gain;
}
