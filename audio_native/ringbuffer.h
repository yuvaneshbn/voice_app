#ifndef RINGBUFFER_H
#define RINGBUFFER_H

#include <cstdint>
#include <vector>

class RingBuffer {
public:
    RingBuffer(int capacity, int frameSize);
    void push(uint16_t seq, const int16_t* frame);
    bool pop(uint16_t seq, int16_t* outFrame);

private:
    struct Slot {
        uint16_t seq = 0;
        bool valid = false;
        std::vector<int16_t> frame;
    };

    int capacity;
    int frameSize;
    std::vector<Slot> buffer;
};

#endif
