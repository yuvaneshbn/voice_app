set(SERVER_SOURCES
    main.cpp
    voice_server.cpp
)

add_executable(voice_server ${SERVER_SOURCES})

target_include_directories(voice_server PRIVATE
    ${CMAKE_SOURCE_DIR}/shared
    ${CMAKE_SOURCE_DIR}/server
    ${OPUS_INCLUDE_DIR}
)

target_link_libraries(voice_server PRIVATE
    voice_shared
    ws2_32
    advapi32
)

target_compile_definitions(voice_server PRIVATE NOMINMAX)

add_custom_command(TARGET voice_server POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${OPUS_DLL}
        $<TARGET_FILE_DIR:voice_server>/opus.dll
)
