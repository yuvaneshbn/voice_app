#include "ringbuffer.h"

#include <algorithm>

RingBuffer::RingBuffer(int capacity, int frameSize)
    : capacity(capacity), frameSize(frameSize), buffer(capacity) {
    for (auto& slot : buffer) {
        slot.frame.resize(frameSize, 0);
    }
}

void RingBuffer::push(uint16_t seq, const int16_t* frame) {
    if (capacity <= 0 || frameSize <= 0 || !frame) {
        return;
    }

    const int index = static_cast<int>(seq % static_cast<uint16_t>(capacity));
    Slot& slot = buffer[index];
    slot.seq = seq;
    slot.valid = true;
    std::copy(frame, frame + frameSize, slot.frame.begin());
}

bool RingBuffer::pop(uint16_t seq, int16_t* outFrame) {
    if (capacity <= 0 || frameSize <= 0 || !outFrame) {
        return false;
    }

    const int index = static_cast<int>(seq % static_cast<uint16_t>(capacity));
    Slot& slot = buffer[index];
    if (!slot.valid || slot.seq != seq) {
        return false;
    }

    std::copy(slot.frame.begin(), slot.frame.end(), outFrame);
    slot.valid = false;
    return true;
}
