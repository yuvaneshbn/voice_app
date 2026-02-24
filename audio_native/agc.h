#ifndef AGC_H
#define AGC_H

#include <cstdint>

class SimpleAGC {
public:
    explicit SimpleAGC(float targetRMS = 3000.0f);
    float process(const int16_t* samples, int frameSize);

private:
    float targetRMS;
    float gain;
};

#endif
