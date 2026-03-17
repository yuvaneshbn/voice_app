set(CLIENT_SOURCES
    main.cpp
    main_window.cpp
    participant_row.cpp
    settings_dialog.cpp
    startup_dialog.cpp
    volume_control_panel.cpp
    audio_engine.cpp
    echo_canceller.cpp
    network_discovery.cpp
    control_client.cpp
    ui_helpers.cpp
)

add_executable(voice_client ${CLIENT_SOURCES})

target_include_directories(voice_client PRIVATE
    ${CMAKE_SOURCE_DIR}/shared
    ${CMAKE_SOURCE_DIR}/client
    ${OPUS_INCLUDE_DIR}
)

target_link_libraries(voice_client PRIVATE
    Qt6::Widgets
    Qt6::UiTools
    voice_shared
    ws2_32
    winmm
)

if (TARGET native_mixer)
    target_link_libraries(voice_client PRIVATE native_mixer)
    target_compile_definitions(voice_client PRIVATE VOICE_HAVE_NATIVE_MIXER=1)
else()
    target_compile_definitions(voice_client PRIVATE VOICE_HAVE_NATIVE_MIXER=0)
endif()

target_compile_definitions(voice_client PRIVATE
    NOMINMAX
    VOICE_PORTAUDIO_DLL_FALLBACK="${PORTAUDIO_DLL}"
)

set(QT_DEPLOY_TOOL "")
if (WINDEPLOYQT_EXECUTABLE AND EXISTS "${WINDEPLOYQT_EXECUTABLE}")
    set(QT_DEPLOY_TOOL "${WINDEPLOYQT_EXECUTABLE}")
elseif (Qt6_DIR)
    get_filename_component(_qt6_cmake_dir "${Qt6_DIR}" ABSOLUTE)
    get_filename_component(_qt6_cmake_root "${_qt6_cmake_dir}" DIRECTORY)
    get_filename_component(_qt6_lib_dir "${_qt6_cmake_root}" DIRECTORY)
    get_filename_component(_qt6_prefix "${_qt6_lib_dir}" DIRECTORY)
    set(QT_DEPLOY_TOOL "${_qt6_prefix}/bin/windeployqt.exe")
endif()

add_custom_command(TARGET voice_client POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
        ${CMAKE_SOURCE_DIR}/client/ui
        $<TARGET_FILE_DIR:voice_client>/ui
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${CMAKE_SOURCE_DIR}/client/technical-support.ico
        $<TARGET_FILE_DIR:voice_client>/technical-support.ico
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${OPUS_DLL}
        $<TARGET_FILE_DIR:voice_client>/opus.dll
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${PORTAUDIO_DLL}
        $<TARGET_FILE_DIR:voice_client>/libportaudio.dll
)

if (WIN32 AND EXISTS "${QT_DEPLOY_TOOL}")
    add_custom_command(TARGET voice_client POST_BUILD
        COMMAND "${QT_DEPLOY_TOOL}"
            --no-compiler-runtime
            --no-translations
            $<$<CONFIG:Debug>:--debug>
            $<$<NOT:$<CONFIG:Debug>>:--release>
            "$<TARGET_FILE:voice_client>"
    )
endif()
