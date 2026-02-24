#include "mixer.h"
#include "ringbuffer.h"

#include <cstddef>
#include <cstdint>

#if defined(_WIN32) || defined(_WIN64)
#define EXPORT_API __declspec(dllexport)
#else
#define EXPORT_API
#endif

extern "C" {
EXPORT_API void mix_frames(
    int16_t** inputs,
    float* gains,
    int numStreams,
    int frameSize,
    int16_t* output
) {
    if (!output || frameSize <= 0) {
        return;
    }

    AudioMixer mixer(frameSize);
    mixer.reset();

    for (int i = 0; i < numStreams; ++i) {
        const int16_t* stream = inputs ? inputs[i] : nullptr;
        const float gain = gains ? gains[i] : 1.0f;
        mixer.addStream(stream, gain);
    }

    mixer.mix(output, numStreams);
}

EXPORT_API void* ringbuffer_create(int capacity, int frameSize) {
    if (capacity <= 0 || frameSize <= 0) {
        return nullptr;
    }
    return new RingBuffer(capacity, frameSize);
}

EXPORT_API void ringbuffer_destroy(void* handle) {
    if (!handle) {
        return;
    }
    auto* rb = static_cast<RingBuffer*>(handle);
    delete rb;
}

EXPORT_API void ringbuffer_push(void* handle, uint16_t seq, const int16_t* frame) {
    if (!handle || !frame) {
        return;
    }
    auto* rb = static_cast<RingBuffer*>(handle);
    rb->push(seq, frame);
}

EXPORT_API int ringbuffer_pop(void* handle, uint16_t seq, int16_t* outFrame) {
    if (!handle || !outFrame) {
        return 0;
    }
    auto* rb = static_cast<RingBuffer*>(handle);
    return rb->pop(seq, outFrame) ? 1 : 0;
}
}
