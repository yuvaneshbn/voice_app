#include "control_client.h"

#include "../shared/socket_utils.h"

#include <winsock2.h>
#include <ws2tcpip.h>

#include <algorithm>
#include <cctype>
#include <iostream>

namespace {
constexpr int CONTROL_PORT = 50001;
constexpr int DSCP_CS3 = 24;
constexpr int IP_TOS_CS3 = DSCP_CS3 << 2;
}

ControlResponse send_control_command(const std::string& server_ip, const std::string& command, int timeout_ms) {
    SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock == INVALID_SOCKET) {
        return {false, "socket failed"};
    }

    socket_utils::set_dscp(sock, IP_TOS_CS3);
    const int nodelay = 1;
    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&nodelay), sizeof(nodelay));

    const DWORD timeout = static_cast<DWORD>(timeout_ms);
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<u_short>(CONTROL_PORT));
    inet_pton(AF_INET, server_ip.c_str(), &addr.sin_addr);

    if (connect(sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == SOCKET_ERROR) {
        closesocket(sock);
        return {false, "connect failed"};
    }

    std::string payload = command + "\n";
    send(sock, payload.c_str(), static_cast<int>(payload.size()), 0);

    char buffer[1024] = {0};
    const int recv_len = recv(sock, buffer, sizeof(buffer) - 1, 0);
    closesocket(sock);

    if (recv_len <= 0) {
        return {false, "no response"};
    }

    std::string response(buffer, buffer + recv_len);
    while (!response.empty() && (response.back() == '\n' || response.back() == '\r')) {
        response.pop_back();
    }
    return {true, response};
}

std::vector<std::string> parse_client_list_response(const std::string& response) {
    std::vector<std::string> out;
    if (response.empty()) {
        return out;
    }
    size_t start = 0;
    while (start < response.size()) {
        size_t end = response.find_first_of("\n,", start);
        std::string token = response.substr(start, end == std::string::npos ? std::string::npos : end - start);
        token.erase(
            std::remove_if(token.begin(), token.end(), [](unsigned char ch) { return std::isspace(ch); }),
            token.end());
        if (!token.empty()) {
            out.push_back(token);
        }
        if (end == std::string::npos) {
            break;
        }
        start = end + 1;
    }
    return out;
}
