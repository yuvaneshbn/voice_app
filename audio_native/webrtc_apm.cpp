#include "webrtc_apm.h"

#include <algorithm>
#include <cstring>

WebRtcApm::WebRtcApm(int sampleRate, int channels, int frameSize)
    : sampleRate(sampleRate),
      channels(channels),
      frameSize(frameSize),
      delayMs(50),
      lastFar(static_cast<size_t>(frameSize), 0) {
}

WebRtcApm::~WebRtcApm() = default;

int WebRtcApm::configure(int enableAec3, int enableNs, int enableAgc, int enableVad) {
    (void)enableAec3;
    (void)enableNs;
    (void)enableAgc;
    (void)enableVad;
    // Placeholder: full WebRTC APM wiring can be enabled in this class.
    return 1;
}

int WebRtcApm::setDelayMs(int delayMs) {
    this->delayMs = std::max(0, delayMs);
    return 1;
}

int WebRtcApm::processReverse(const int16_t* farFrame, int frameSamples) {
    if (!farFrame || frameSamples <= 0) {
        return 0;
    }
    const int n = std::min(frameSize, frameSamples);
    std::memcpy(lastFar.data(), farFrame, static_cast<size_t>(n) * sizeof(int16_t));
    return 1;
}

int WebRtcApm::processCapture(const int16_t* nearFrame, int frameSamples, int16_t* outFrame) {
    if (!nearFrame || !outFrame || frameSamples <= 0) {
        return 0;
    }
    const int n = std::min(frameSize, frameSamples);
    std::memcpy(outFrame, nearFrame, static_cast<size_t>(n) * sizeof(int16_t));

    // Placeholder passthrough. This class is where full AEC3/NS should be linked and run.
    // Keep behavior transparent until WebRTC deps are added.
    (void)sampleRate;
    (void)channels;
    (void)delayMs;
    (void)lastFar;
    return 1;
}
