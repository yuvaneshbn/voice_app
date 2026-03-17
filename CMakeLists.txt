cmake_minimum_required(VERSION 3.20)
project(voice_two_way LANGUAGES C CXX)

if (POLICY CMP0144)
    cmake_policy(SET CMP0144 NEW)
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)

option(VOICE_BUILD_CLIENT "Build voice client" ON)
option(VOICE_BUILD_SERVER "Build voice server" ON)
option(VOICE_BUILD_AUDIO_NATIVE "Build native_mixer audio module" OFF)

if (MINGW AND VOICE_BUILD_AUDIO_NATIVE)
    message(WARNING "MinGW toolchains often fail to build the bundled AEC3; disabling VOICE_BUILD_AUDIO_NATIVE.")
    set(VOICE_BUILD_AUDIO_NATIVE OFF CACHE BOOL "Build native_mixer audio module" FORCE)
endif()

set(QT6_ROOT "C:/Qt/6.10.2" CACHE PATH "Qt6 install root (e.g. C:/Qt/6.10.2)")
if (QT6_ROOT)
    if (MINGW)
        set(_qt6_candidates
            "${QT6_ROOT}/mingw_64/lib/cmake/Qt6"
            "${QT6_ROOT}/lib/cmake/Qt6"
        )
    else()
        set(_qt6_candidates
            "${QT6_ROOT}/msvc2022_64/lib/cmake/Qt6"
            "${QT6_ROOT}/msvc2019_64/lib/cmake/Qt6"
            "${QT6_ROOT}/lib/cmake/Qt6"
        )
    endif()
    unset(Qt6_DIR CACHE)
    foreach(_qt6_path IN LISTS _qt6_candidates)
        if (EXISTS "${_qt6_path}/Qt6Config.cmake")
            set(Qt6_DIR "${_qt6_path}" CACHE PATH "Qt6 CMake package directory" FORCE)
            break()
        endif()
    endforeach()
endif()
if (QT6_ROOT)
    list(PREPEND CMAKE_PREFIX_PATH "${QT6_ROOT}")
endif()

set(OPUS_DIR ${CMAKE_SOURCE_DIR}/third_party/opus)
set(OPUS_INCLUDE_DIR ${OPUS_DIR})
set(OPUS_DLL ${OPUS_DIR}/opus.dll)
set(OPUS_LIB ${OPUS_DIR}/opus.lib)
set(PORTAUDIO_DLL "C:/msys64/ucrt64/bin/libportaudio.dll" CACHE FILEPATH "PortAudio runtime DLL")

if (EXISTS ${OPUS_LIB})
    add_library(opus SHARED IMPORTED)
    set_target_properties(opus PROPERTIES
        IMPORTED_IMPLIB ${OPUS_LIB}
        IMPORTED_LOCATION ${OPUS_DLL}
    )
else()
    message(FATAL_ERROR "Missing Opus import library: ${OPUS_LIB}")
endif()

add_library(voice_shared STATIC
    shared/opus_codec.cpp
    shared/socket_utils.cpp
    shared/winsock_init.cpp
)
target_include_directories(voice_shared PUBLIC ${CMAKE_SOURCE_DIR}/shared ${OPUS_INCLUDE_DIR})
target_link_libraries(voice_shared PUBLIC opus ws2_32)

if (VOICE_BUILD_AUDIO_NATIVE)
    add_subdirectory(audio_native)
endif()

if (VOICE_BUILD_CLIENT)
    find_package(Qt6 REQUIRED COMPONENTS Widgets UiTools)
    add_subdirectory(client)
endif()

if (VOICE_BUILD_SERVER)
    add_subdirectory(server)
endif()



