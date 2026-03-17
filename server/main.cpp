#include "voice_server.h"
#include "../shared/winsock_init.h"

#include <windows.h>

#include <chrono>
#include <iostream>
#include <thread>

namespace {
VoiceServer* g_server = nullptr;

BOOL WINAPI console_handler(DWORD type) {
    if (type == CTRL_C_EVENT || type == CTRL_CLOSE_EVENT || type == CTRL_BREAK_EVENT) {
        if (g_server) {
            g_server->stop();
        }
        return TRUE;
    }
    return FALSE;
}
} // namespace

int main() {
    WinSockInit wsa;
    if (!wsa.ok()) {
        std::cerr << "[SERVER] Winsock initialization failed\n";
        return 1;
    }

    VoiceServer server;
    g_server = &server;
    SetConsoleCtrlHandler(console_handler, TRUE);

    if (!server.start()) {
        std::cerr << "[SERVER] Failed to start\n";
        return 1;
    }

    std::cout << "[SERVER] Running. Press Ctrl+C to stop.\n";
    while (server.isRunning()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
    std::cout << "[SERVER] Stopped.\n";
    return 0;
}
