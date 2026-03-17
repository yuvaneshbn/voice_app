#ifndef CLIENT_PORTAUDIO_DYN_H
#define CLIENT_PORTAUDIO_DYN_H

typedef int PaError;
typedef int PaDeviceIndex;
typedef int PaHostApiIndex;
typedef int PaHostApiTypeId;
typedef double PaTime;
typedef unsigned long PaSampleFormat;
typedef unsigned long PaStreamFlags;
typedef unsigned long PaStreamCallbackFlags;
typedef void PaStream;

enum {
    paNoError = 0
};

#define paNoDevice ((PaDeviceIndex)-1)
#define paInt16 ((PaSampleFormat)0x00000008)
#define paNoFlag ((PaStreamFlags)0)

typedef struct PaHostApiInfo {
    int structVersion;
    PaHostApiTypeId type;
    const char* name;
    int deviceCount;
    PaDeviceIndex defaultInputDevice;
    PaDeviceIndex defaultOutputDevice;
} PaHostApiInfo;

typedef struct PaDeviceInfo {
    int structVersion;
    const char* name;
    PaHostApiIndex hostApi;
    int maxInputChannels;
    int maxOutputChannels;
    PaTime defaultLowInputLatency;
    PaTime defaultLowOutputLatency;
    PaTime defaultHighInputLatency;
    PaTime defaultHighOutputLatency;
    double defaultSampleRate;
} PaDeviceInfo;

typedef struct PaStreamParameters {
    PaDeviceIndex device;
    int channelCount;
    PaSampleFormat sampleFormat;
    PaTime suggestedLatency;
    void* hostApiSpecificStreamInfo;
} PaStreamParameters;

typedef struct PaStreamCallbackTimeInfo {
    PaTime inputBufferAdcTime;
    PaTime currentTime;
    PaTime outputBufferDacTime;
} PaStreamCallbackTimeInfo;

typedef int PaStreamCallback(
    const void* input,
    void* output,
    unsigned long frameCount,
    const PaStreamCallbackTimeInfo* timeInfo,
    PaStreamCallbackFlags statusFlags,
    void* userData);

enum {
    paContinue = 0
};

#endif
