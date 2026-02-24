#ifndef WEBRTC_APM_H
#define WEBRTC_APM_H

#include <cstdint>
#include <vector>

class WebRtcApm {
public:
    WebRtcApm(int sampleRate, int channels, int frameSize);
    ~WebRtcApm();

    int configure(int enableAec3, int enableNs, int enableAgc, int enableVad);
    int setDelayMs(int delayMs);
    int processReverse(const int16_t* farFrame, int frameSamples);
    int processCapture(const int16_t* nearFrame, int frameSamples, int16_t* outFrame);

private:
    int sampleRate;
    int channels;
    int frameSize;
    int delayMs;
    std::vector<int16_t> lastFar;
};

#endif
