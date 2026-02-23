cmake_minimum_required(VERSION 3.15)
project(native_mixer LANGUAGES CXX)

add_library(native_mixer SHARED
    bindings.cpp
    mixer.cpp
    agc.cpp
    ringbuffer.cpp
)

target_compile_features(native_mixer PRIVATE cxx_std_17)

