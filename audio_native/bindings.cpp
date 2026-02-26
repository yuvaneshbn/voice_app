#include "mixer.h"
#include "ringbuffer.h"
#include "webrtc_apm.h"
#include "agc.h"

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

EXPORT_API void* apm_create(int sampleRate, int channels, int frameSize) {
    try {
        if (sampleRate <= 0 || channels <= 0 || frameSize <= 0) {
            return nullptr;
        }
        return new WebRtcApm(sampleRate, channels, frameSize);
    } catch (...) {
        return nullptr;
    }
}

EXPORT_API void apm_destroy(void* handle) {
    try {
        if (!handle) {
            return;
        }
        auto* apm = static_cast<WebRtcApm*>(handle);
        delete apm;
    } catch (...) {
        return;
    }
}

EXPORT_API int apm_config(void* handle, int enableAec3, int enableNs, int enableAgc, int enableVad) {
    try {
        if (!handle) {
            return 0;
        }
        auto* apm = static_cast<WebRtcApm*>(handle);
        return apm->configure(enableAec3, enableNs, enableAgc, enableVad);
    } catch (...) {
        return 0;
    }
}

EXPORT_API int apm_set_delay_ms(void* handle, int delayMs) {
    try {
        if (!handle) {
            return 0;
        }
        auto* apm = static_cast<WebRtcApm*>(handle);
        return apm->setDelayMs(delayMs);
    } catch (...) {
        return 0;
    }
}

EXPORT_API int apm_process_reverse(void* handle, const int16_t* farFrame, int frameSamples) {
    try {
        if (!handle || !farFrame || frameSamples <= 0) {
            return 0;
        }
        auto* apm = static_cast<WebRtcApm*>(handle);
        return apm->processReverse(farFrame, frameSamples);
    } catch (...) {
        return 0;
    }
}

EXPORT_API int apm_process_capture(void* handle, const int16_t* nearFrame, int frameSamples, int16_t* outFrame) {
    try {
        if (!handle || !nearFrame || !outFrame || frameSamples <= 0) {
            return 0;
        }
        auto* apm = static_cast<WebRtcApm*>(handle);
        return apm->processCapture(nearFrame, frameSamples, outFrame);
    } catch (...) {
        return 0;
    }
}

EXPORT_API int apm_get_metrics(void* handle, float* erl, float* erle, int* delayMs) {
    try {
        if (!handle || !erl || !erle || !delayMs) {
            return 0;
        }
        auto* apm = static_cast<WebRtcApm*>(handle);
        return apm->getMetrics(erl, erle, delayMs);
    } catch (...) {
        return 0;
    }
}

EXPORT_API void* agc_create(float targetRMS) {
    try {
        return new SimpleAGC(targetRMS);
    } catch (...) {
        return nullptr;
    }
}

EXPORT_API void agc_destroy(void* handle) {
    try {
        if (!handle) {
            return;
        }
        auto* agc = static_cast<SimpleAGC*>(handle);
        delete agc;
    } catch (...) {
        return;
    }
}

EXPORT_API float agc_process(void* handle, const int16_t* samples, int frameSize) {
    try {
        if (!handle || !samples || frameSize <= 0) {
            return 1.0f;
        }
        auto* agc = static_cast<SimpleAGC*>(handle);
        return agc->process(samples, frameSize);
    } catch (...) {
        return 1.0f;
    }
}
}
