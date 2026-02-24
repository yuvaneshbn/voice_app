#ifndef MIXER_H
#define MIXER_H

#include <cstdint>
#include <vector>

class AudioMixer {
public:
    explicit AudioMixer(int frameSize);
    void reset();
    void addStream(const int16_t* samples, float gain);
    void mix(int16_t* output, int activeStreams);

private:
    int frameSize;
    std::vector<int32_t> accumulator;
};

#endif
