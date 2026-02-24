cmake_minimum_required(VERSION 3.15)
project(native_mixer LANGUAGES C CXX)

set(_TWOWAY_AEC3_DEFAULT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/AEC3")
if ((NOT DEFINED TWOWAY_AEC3_DIR) OR (NOT EXISTS "${TWOWAY_AEC3_DIR}/api/echo_canceller3_factory.h"))
    set(TWOWAY_AEC3_DIR "${_TWOWAY_AEC3_DEFAULT_DIR}" CACHE PATH "Path to vendored ewan-xu/AEC3 source tree" FORCE)
else()
    set(TWOWAY_AEC3_DIR "${TWOWAY_AEC3_DIR}" CACHE PATH "Path to vendored ewan-xu/AEC3 source tree")
endif()
if (NOT EXISTS "${TWOWAY_AEC3_DIR}/api/echo_canceller3_factory.h")
    message(FATAL_ERROR "AEC3 is required. Missing: ${TWOWAY_AEC3_DIR}/api/echo_canceller3_factory.h")
endif()

add_library(native_mixer SHARED
    bindings.cpp
    mixer.cpp
    agc.cpp
    ringbuffer.cpp
    webrtc_apm.cpp
)

target_compile_features(native_mixer PRIVATE cxx_std_17)

set(AEC3_SOURCES
        ${TWOWAY_AEC3_DIR}/api/echo_canceller3_config.cc
        ${TWOWAY_AEC3_DIR}/api/echo_canceller3_config_json.cc
        ${TWOWAY_AEC3_DIR}/api/echo_canceller3_factory.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/adaptive_fir_filter.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/adaptive_fir_filter_erl.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/aec3_common.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/aec3_fft.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/aec_state.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/alignment_mixer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/api_call_jitter_metrics.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/block_buffer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/block_delay_buffer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/block_framer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/block_processor.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/block_processor_metrics.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/clockdrift_detector.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/comfort_noise_generator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/decimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/dominant_nearend_detector.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/downsampled_render_buffer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/echo_audibility.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/echo_canceller3.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/echo_path_delay_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/echo_path_variability.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/echo_remover.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/echo_remover_metrics.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/erle_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/erl_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/fft_buffer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/filter_analyzer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/frame_blocker.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/fullband_erle_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/main_filter_update_gain.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/matched_filter.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/matched_filter_lag_aggregator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/moving_average.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/render_buffer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/render_delay_buffer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/render_delay_controller.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/render_delay_controller_metrics.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/render_signal_analyzer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/residual_echo_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/reverb_decay_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/reverb_frequency_response.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/reverb_model.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/reverb_model_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/shadow_filter_update_gain.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/signal_dependent_erle_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/spectrum_buffer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/stationarity_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/subband_erle_estimator.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/subband_nearend_detector.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/subtractor.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/subtractor_output.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/subtractor_output_analyzer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/suppression_filter.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3/suppression_gain.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/audio_buffer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/audio_frame.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/channel_buffer.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/channel_layout.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/high_pass_filter.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/logging/apm_data_dumper.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/logging/wav_file.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/logging/wav_header.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/resampler/push_sinc_resampler.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/resampler/sinc_resampler.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/resampler/sinc_resampler_neon.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/resampler/sinc_resampler_sse.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/sparse_fir_filter.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/splitting_filter_c.c
        ${TWOWAY_AEC3_DIR}/audio_processing/splitting_filter.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/three_band_filter_bank.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/utility/cascaded_biquad_filter.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/utility/ooura_fft.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/utility/ooura_fft_mips.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/utility/ooura_fft_neon.cc
        ${TWOWAY_AEC3_DIR}/audio_processing/utility/ooura_fft_sse2.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/base/internal/throw_delegate.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/base/internal/raw_logging.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/numeric/int128.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/ascii.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/charconv.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/escaping.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/internal/charconv_bigint.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/internal/charconv_parse.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/internal/memutil.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/internal/utf8.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/match.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/numbers.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/string_view.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/str_cat.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/str_replace.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/str_split.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/strings/substitute.cc
        ${TWOWAY_AEC3_DIR}/base/abseil/absl/types/bad_optional_access.cc
        ${TWOWAY_AEC3_DIR}/base/jsoncpp/src/lib_json/json_reader.cpp
        ${TWOWAY_AEC3_DIR}/base/jsoncpp/src/lib_json/json_value.cpp
        ${TWOWAY_AEC3_DIR}/base/jsoncpp/src/lib_json/json_writer.cpp
        ${TWOWAY_AEC3_DIR}/base/rtc_base/checks.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/critical_section.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/logging.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/memory/aligned_malloc.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/platform_thread_types.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/race_checker.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/strings/json.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/strings/string_builder.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/string_encode.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/string_to_number.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/string_utils.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/system/file_wrapper.cc
        ${TWOWAY_AEC3_DIR}/base/rtc_base/time_utils.cc
        ${TWOWAY_AEC3_DIR}/base/system_wrappers/source/cpu_features.cc
        ${TWOWAY_AEC3_DIR}/base/system_wrappers/source/cpu_features_android.c
        ${TWOWAY_AEC3_DIR}/base/system_wrappers/source/cpu_features_linux.c
        ${TWOWAY_AEC3_DIR}/base/system_wrappers/source/field_trial.cc
        ${TWOWAY_AEC3_DIR}/base/system_wrappers/source/metrics.cc
    )

    target_sources(native_mixer PRIVATE ${AEC3_SOURCES})
    if (MSVC)
        set_source_files_properties(
            ${TWOWAY_AEC3_DIR}/audio_processing/aec3/reverb_model_estimator.cc
            PROPERTIES COMPILE_OPTIONS "/FI\"memory\""
        )
    else()
        set_source_files_properties(
            ${TWOWAY_AEC3_DIR}/audio_processing/aec3/reverb_model_estimator.cc
            PROPERTIES COMPILE_OPTIONS "-include memory"
        )
    endif()
    target_include_directories(native_mixer PRIVATE
        ${TWOWAY_AEC3_DIR}
        ${TWOWAY_AEC3_DIR}/base
        ${TWOWAY_AEC3_DIR}/base/abseil
        ${TWOWAY_AEC3_DIR}/base/jsoncpp/include
        ${TWOWAY_AEC3_DIR}/audio_processing
        ${TWOWAY_AEC3_DIR}/audio_processing/aec3
    )
    target_compile_definitions(native_mixer PRIVATE
        TWOWAY_HAS_AEC3
        WEBRTC_WIN
        WIN32
        _WINDOWS
        _USE_MATH_DEFINES
        NOMINMAX
        _CRT_SECURE_NO_WARNINGS
        _SCL_SECURE_NO_WARNINGS
        WIN32_LEAN_AND_MEAN
    )
    if (MSVC)
        target_compile_options(native_mixer PRIVATE
            /wd4005
            /wd4068
            /wd4180
            /wd4244
            /wd4267
            /wd4503
            /wd4800
        )
    endif()
if (WIN32)
    target_link_libraries(native_mixer PRIVATE winmm)
endif()
