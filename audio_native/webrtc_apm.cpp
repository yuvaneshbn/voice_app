#include "webrtc_apm.h"

#include <algorithm>
#include <atomic>
#include <cstring>
#include <mutex>
#include <vector>

#include "api/echo_canceller3_config.h"
#include "api/echo_canceller3_factory.h"
#include "audio_processing/audio_buffer.h"
#include "audio_processing/audio_frame.h"

class WebRtcApm::Impl {
public:
    Impl(int sampleRate, int channels, int delayMs)
        : sampleRate(sampleRate),
          channels(channels),
          delayMs(delayMs),
          samplesPer10ms(sampleRate / 100),
          streamConfig(sampleRate, channels, false),
          renderScratch(static_cast<size_t>(std::max(1, sampleRate / 100))),
          captureScratch(static_cast<size_t>(std::max(1, sampleRate / 100))) {
        if (sampleRate <= 0 || channels <= 0 || sampleRate % 100 != 0) {
            return;
        }

        webrtc::EchoCanceller3Config config;
        config.filter.export_linear_aec_output = false;
        // Let AEC3 estimate/render-align delay internally for better convergence.
        config.delay.use_external_delay_estimator = false;
        // Slightly more aggressive adaptation/suppression for speaker echo paths.
        config.erle.max_l = 8.f;
        config.erle.max_h = 4.f;
        config.ep_strength.default_gain = 1.2f;
        config.suppressor.high_bands_suppression.max_gain_during_echo = 0.25f;
        config.suppressor.floor_first_increase = 0.000001f;
        useExternalDelayEstimator = config.delay.use_external_delay_estimator;
        factory = std::make_unique<webrtc::EchoCanceller3Factory>(config);
        echo = factory->Create(sampleRate, channels, channels);

        renderAudio = std::make_unique<webrtc::AudioBuffer>(
            streamConfig.sample_rate_hz(),
            streamConfig.num_channels(),
            streamConfig.sample_rate_hz(),
            streamConfig.num_channels(),
            streamConfig.sample_rate_hz(),
            streamConfig.num_channels());

        captureAudio = std::make_unique<webrtc::AudioBuffer>(
            streamConfig.sample_rate_hz(),
            streamConfig.num_channels(),
            streamConfig.sample_rate_hz(),
            streamConfig.num_channels(),
            streamConfig.sample_rate_hz(),
            streamConfig.num_channels());

        initialized = static_cast<bool>(echo && renderAudio && captureAudio);
    }

    bool isReady() const { return initialized; }

    void setDelayMs(int d) { delayMs.store(std::max(0, d), std::memory_order_relaxed); }

    void processRenderFrame(const int16_t* frame, int frameSamples) {
        if (!initialized || !frame || frameSamples <= 0) {
            return;
        }
        std::lock_guard<std::mutex> lock(procMutex);
        try {
            feedRenderChunks(frame, frameSamples);
        } catch (...) {
            // Prevent C++ exceptions from crossing FFI boundary.
        }
    }

    void processCaptureFrame(const int16_t* inFrame, int frameSamples, int16_t* outFrame) {
        if (!inFrame || !outFrame || frameSamples <= 0) {
            return;
        }
        std::memcpy(outFrame, inFrame, static_cast<size_t>(frameSamples) * sizeof(int16_t));
        if (!initialized) {
            return;
        }
        std::lock_guard<std::mutex> lock(procMutex);
        try {
            feedCaptureChunks(outFrame, frameSamples);
        } catch (...) {
            // Prevent C++ exceptions from crossing FFI boundary.
        }
    }

    bool getMetrics(float* erl, float* erle, int* outDelayMs) const {
        if (!initialized || !echo || !erl || !erle || !outDelayMs) {
            return false;
        }
        std::lock_guard<std::mutex> lock(procMutex);
        const auto m = echo->GetMetrics();
        *erl = static_cast<float>(m.echo_return_loss);
        *erle = static_cast<float>(m.echo_return_loss_enhancement);
        *outDelayMs = m.delay_ms;
        return true;
    }

private:
    void analyzeRender10ms(const int16_t* frame10ms) {
        std::memcpy(renderScratch.data(), frame10ms, static_cast<size_t>(samplesPer10ms) * sizeof(int16_t));
        renderFrame.UpdateFrame(
            0,
            renderScratch.data(),
            samplesPer10ms,
            sampleRate,
            webrtc::AudioFrame::kNormalSpeech,
            webrtc::AudioFrame::kVadPassive,
            channels);
        renderAudio->CopyFrom(&renderFrame);
        renderAudio->SplitIntoFrequencyBands();
        echo->AnalyzeRender(renderAudio.get());
        renderAudio->MergeFrequencyBands();
    }

    void processCapture10ms(int16_t* frame10ms) {
        std::memcpy(captureScratch.data(), frame10ms, static_cast<size_t>(samplesPer10ms) * sizeof(int16_t));
        captureFrame.UpdateFrame(
            0,
            captureScratch.data(),
            samplesPer10ms,
            sampleRate,
            webrtc::AudioFrame::kNormalSpeech,
            webrtc::AudioFrame::kVadActive,
            channels);
        captureAudio->CopyFrom(&captureFrame);
        echo->AnalyzeCapture(captureAudio.get());
        captureAudio->SplitIntoFrequencyBands();
        if (useExternalDelayEstimator) {
            echo->SetAudioBufferDelay(delayMs.load(std::memory_order_relaxed));
        }
        echo->ProcessCapture(captureAudio.get(), false);
        captureAudio->MergeFrequencyBands();
        captureAudio->CopyTo(&captureFrame);
        std::memcpy(frame10ms, captureFrame.data(), static_cast<size_t>(samplesPer10ms) * sizeof(int16_t));
    }

    void feedRenderChunks(const int16_t* frame, int frameSamples) {
        for (int offset = 0; offset < frameSamples; offset += samplesPer10ms) {
            const int remaining = frameSamples - offset;
            if (remaining >= samplesPer10ms) {
                analyzeRender10ms(frame + offset);
                continue;
            }

            std::memset(renderScratch.data(), 0, static_cast<size_t>(samplesPer10ms) * sizeof(int16_t));
            std::memcpy(
                renderScratch.data(),
                frame + offset,
                static_cast<size_t>(remaining) * sizeof(int16_t));
            analyzeRender10ms(renderScratch.data());
        }
    }

    void feedCaptureChunks(int16_t* frame, int frameSamples) {
        for (int offset = 0; offset < frameSamples; offset += samplesPer10ms) {
            const int remaining = frameSamples - offset;
            if (remaining >= samplesPer10ms) {
                processCapture10ms(frame + offset);
                continue;
            }

            std::memset(captureScratch.data(), 0, static_cast<size_t>(samplesPer10ms) * sizeof(int16_t));
            std::memcpy(
                captureScratch.data(),
                frame + offset,
                static_cast<size_t>(remaining) * sizeof(int16_t));
            processCapture10ms(captureScratch.data());
            std::memcpy(
                frame + offset,
                captureScratch.data(),
                static_cast<size_t>(remaining) * sizeof(int16_t));
        }
    }

    int sampleRate;
    int channels;
    std::atomic<int> delayMs;
    int samplesPer10ms;
    bool initialized = false;
    webrtc::StreamConfig streamConfig;
    std::unique_ptr<webrtc::EchoCanceller3Factory> factory;
    std::unique_ptr<webrtc::EchoControl> echo;
    std::unique_ptr<webrtc::AudioBuffer> renderAudio;
    std::unique_ptr<webrtc::AudioBuffer> captureAudio;
    webrtc::AudioFrame renderFrame;
    webrtc::AudioFrame captureFrame;
    std::vector<int16_t> renderScratch;
    std::vector<int16_t> captureScratch;
    bool useExternalDelayEstimator = false;
    mutable std::mutex procMutex;
};

WebRtcApm::WebRtcApm(int sampleRate, int channels, int frameSize)
    : sampleRate(sampleRate),
      channels(channels),
      frameSize(frameSize),
      delayMs(50),
      lastFar(static_cast<size_t>(frameSize), 0),
      enableAec3(1),
      enableNs(1),
      enableAgc(0),
      enableVad(0) {
    impl = std::make_unique<Impl>(sampleRate, channels, delayMs);
}

WebRtcApm::~WebRtcApm() = default;

int WebRtcApm::configure(int enableAec3, int enableNs, int enableAgc, int enableVad) {
    this->enableAec3 = enableAec3 ? 1 : 0;
    this->enableNs = enableNs ? 1 : 0;
    this->enableAgc = enableAgc ? 1 : 0;
    this->enableVad = enableVad ? 1 : 0;
    return 1;
}

int WebRtcApm::setDelayMs(int delayMs) {
    this->delayMs = std::max(0, delayMs);
    if (impl) {
        impl->setDelayMs(this->delayMs);
    }
    return 1;
}

int WebRtcApm::processReverse(const int16_t* farFrame, int frameSamples) {
    if (!farFrame || frameSamples <= 0) {
        return 0;
    }
    const int n = std::min(frameSize, frameSamples);
    std::memcpy(lastFar.data(), farFrame, static_cast<size_t>(n) * sizeof(int16_t));
    if (enableAec3 && impl && impl->isReady()) {
        impl->processRenderFrame(lastFar.data(), n);
    }
    return 1;
}

int WebRtcApm::processCapture(const int16_t* nearFrame, int frameSamples, int16_t* outFrame) {
    if (!nearFrame || !outFrame || frameSamples <= 0) {
        return 0;
    }
    const int n = std::min(frameSize, frameSamples);
    std::memcpy(outFrame, nearFrame, static_cast<size_t>(n) * sizeof(int16_t));
    if (enableAec3 && impl && impl->isReady()) {
        impl->processCaptureFrame(outFrame, n, outFrame);
    }
    return 1;
}

int WebRtcApm::getMetrics(float* erl, float* erle, int* outDelayMs) const {
    if (!erl || !erle || !outDelayMs || !impl || !impl->isReady()) {
        return 0;
    }
    return impl->getMetrics(erl, erle, outDelayMs) ? 1 : 0;
}
