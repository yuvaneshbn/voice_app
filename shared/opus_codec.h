#ifndef OPUS_CODEC_H
#define OPUS_CODEC_H

#include <opus.h>

#include <cstdint>
#include <vector>

class OpusCodec {
public:
    OpusCodec(int rate = 16000,
              int channels = 1,
              int frame_size = 320,
              bool enable_fec = true,
              int packet_loss_perc = 15,
              int bitrate = 48000,
              int complexity = 10,
              bool enable_dtx = true,
              int application = OPUS_APPLICATION_AUDIO,
              bool create_encoder = true,
              bool create_decoder = true);

    ~OpusCodec();

    OpusCodec(const OpusCodec&) = delete;
    OpusCodec& operator=(const OpusCodec&) = delete;

    int frame_size() const { return frame_size_; }

    std::vector<uint8_t> encode(const int16_t* pcm, int frame_samples);
    std::vector<uint8_t> encode(const std::vector<int16_t>& pcm);

    std::vector<int16_t> decode(const uint8_t* data, int len);
    std::vector<int16_t> decode(const std::vector<uint8_t>& data);

private:
    void set_encoder_ctl(int request, int value);

    int frame_size_ = 320;
    OpusEncoder* encoder_ = nullptr;
    OpusDecoder* decoder_ = nullptr;
};

#endif
