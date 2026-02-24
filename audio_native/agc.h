#ifndef AGC_H
#define AGC_H

#include <cstdint>

class SimpleAGC {
public:
    explicit SimpleAGC(float targetPeak = 12000.0f);
    float process(const int16_t* samples, int frameSize);

private:
    float emaLevel;
    float targetPeak;
};

#endif
