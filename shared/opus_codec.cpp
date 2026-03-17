#include "opus_codec.h"

#include <algorithm>
#include <iostream>
#include <stdexcept>

OpusCodec::OpusCodec(int rate,
                     int channels,
                     int frame_size,
                     bool enable_fec,
                     int packet_loss_perc,
                     int bitrate,
                     int complexity,
                     bool enable_dtx,
                     int application,
                     bool create_encoder,
                     bool create_decoder)
    : frame_size_(frame_size) {
    int err = OPUS_OK;
    if (create_encoder) {
        encoder_ = opus_encoder_create(rate, channels, application, &err);
        if (!encoder_ || err != OPUS_OK) {
            encoder_ = nullptr;
            throw std::runtime_error("Opus encoder creation failed");
        }
        set_encoder_ctl(OPUS_SET_VBR_REQUEST, 1);
        if (bitrate > 0) {
            set_encoder_ctl(OPUS_SET_BITRATE_REQUEST, bitrate);
        }
        if (complexity >= 0) {
            set_encoder_ctl(OPUS_SET_COMPLEXITY_REQUEST, complexity);
        }
        set_encoder_ctl(OPUS_SET_INBAND_FEC_REQUEST, enable_fec ? 1 : 0);
        (void)enable_dtx;
        if (packet_loss_perc > 0) {
            set_encoder_ctl(OPUS_SET_PACKET_LOSS_PERC_REQUEST, packet_loss_perc);
        }
    }

    if (create_decoder) {
        decoder_ = opus_decoder_create(rate, channels, &err);
        if (!decoder_ || err != OPUS_OK) {
            decoder_ = nullptr;
            throw std::runtime_error("Opus decoder creation failed");
        }
    }
}

OpusCodec::~OpusCodec() {
    if (encoder_) {
        opus_encoder_destroy(encoder_);
        encoder_ = nullptr;
    }
    if (decoder_) {
        opus_decoder_destroy(decoder_);
        decoder_ = nullptr;
    }
}

void OpusCodec::set_encoder_ctl(int request, int value) {
    if (!encoder_) {
        return;
    }
    const int rc = opus_encoder_ctl(encoder_, request, value);
    if (rc != OPUS_OK) {
        std::cerr << "[OPUS] encoder_ctl request=" << request << " value=" << value << " rc=" << rc << "\n";
    }
}

std::vector<uint8_t> OpusCodec::encode(const int16_t* pcm, int frame_samples) {
    if (!encoder_) {
        return {};
    }
    if (frame_samples <= 0) {
        return {};
    }

    std::vector<int16_t> temp;
    const int needed = frame_size_;
    if (frame_samples != needed) {
        temp.assign(needed, 0);
        const int copy_count = std::min(frame_samples, needed);
        std::copy(pcm, pcm + copy_count, temp.begin());
        pcm = temp.data();
        frame_samples = needed;
    }

    std::vector<uint8_t> out(4000);
    const int size = opus_encode(encoder_, pcm, frame_samples, out.data(), static_cast<int>(out.size()));
    if (size < 0) {
        return {};
    }
    out.resize(static_cast<size_t>(size));
    return out;
}

std::vector<uint8_t> OpusCodec::encode(const std::vector<int16_t>& pcm) {
    if (pcm.empty()) {
        return {};
    }
    return encode(pcm.data(), static_cast<int>(pcm.size()));
}

std::vector<int16_t> OpusCodec::decode(const uint8_t* data, int len) {
    if (!decoder_) {
        return {};
    }
    std::vector<int16_t> pcm(frame_size_, 0);
    const int decoded = opus_decode(decoder_, data, len, pcm.data(), frame_size_, 0);
    if (decoded < 0) {
        return {};
    }
    pcm.resize(static_cast<size_t>(decoded));
    return pcm;
}

std::vector<int16_t> OpusCodec::decode(const std::vector<uint8_t>& data) {
    if (data.empty()) {
        return decode(nullptr, 0);
    }
    return decode(data.data(), static_cast<int>(data.size()));
}
