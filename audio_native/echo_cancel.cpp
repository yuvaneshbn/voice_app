#include "webrtc_apm.h"

#include <cstdint>

#if defined(_WIN32) || defined(_WIN64)
#define EXPORT_API __declspec(dllexport)
#else
#define EXPORT_API
#endif

extern "C" {
EXPORT_API void* ec_create(int sampleRate, int channels, int frameSize) {
    try {
        if (sampleRate <= 0 || channels <= 0 || frameSize <= 0) {
            return nullptr;
        }
        auto* ec = new WebRtcApm(sampleRate, channels, frameSize);
        ec->configure(1, 1, 0, 0);
        ec->setDelayMs(60);
        return ec;
    } catch (...) {
        return nullptr;
    }
}

EXPORT_API void ec_destroy(void* handle) {
    try {
        if (!handle) {
            return;
        }
        auto* ec = static_cast<WebRtcApm*>(handle);
        delete ec;
    } catch (...) {
        return;
    }
}

EXPORT_API int ec_set_delay_ms(void* handle, int delayMs) {
    try {
        if (!handle) {
            return 0;
        }
        auto* ec = static_cast<WebRtcApm*>(handle);
        return ec->setDelayMs(delayMs);
    } catch (...) {
        return 0;
    }
}

EXPORT_API int ec_process_reverse(void* handle, const int16_t* farFrame, int frameSamples) {
    try {
        if (!handle || !farFrame || frameSamples <= 0) {
            return 0;
        }
        auto* ec = static_cast<WebRtcApm*>(handle);
        return ec->processReverse(farFrame, frameSamples);
    } catch (...) {
        return 0;
    }
}

EXPORT_API int ec_process_capture(void* handle, const int16_t* nearFrame, int frameSamples, int16_t* outFrame) {
    try {
        if (!handle || !nearFrame || !outFrame || frameSamples <= 0) {
            return 0;
        }
        auto* ec = static_cast<WebRtcApm*>(handle);
        return ec->processCapture(nearFrame, frameSamples, outFrame);
    } catch (...) {
        return 0;
    }
}

EXPORT_API int ec_get_metrics(void* handle, float* erl, float* erle, int* delayMs) {
    try {
        if (!handle || !erl || !erle || !delayMs) {
            return 0;
        }
        auto* ec = static_cast<WebRtcApm*>(handle);
        return ec->getMetrics(erl, erle, delayMs);
    } catch (...) {
        return 0;
    }
}
}
