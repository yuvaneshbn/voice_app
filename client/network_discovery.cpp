#include "network_discovery.h"

#include "../shared/socket_utils.h"

#include <winsock2.h>
#include <ws2tcpip.h>

#include <chrono>
#include <cstring>
#include <iostream>
#include <vector>

namespace {
constexpr int DISCOVERY_PORT = 50000;
constexpr int DSCP_CS3 = 24;
constexpr int IP_TOS_CS3 = DSCP_CS3 << 2;
}

bool NetworkDiscovery::discover(double timeout_seconds) {
    server_ip_.clear();
    SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock == INVALID_SOCKET) {
        return false;
    }

    const int reuse = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
    socket_utils::set_dscp(sock, IP_TOS_CS3);

    sockaddr_in bind_addr{};
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_port = htons(static_cast<u_short>(DISCOVERY_PORT));
    bind_addr.sin_addr.s_addr = htonl(INADDR_ANY);

    if (bind(sock, reinterpret_cast<sockaddr*>(&bind_addr), sizeof(bind_addr)) == SOCKET_ERROR) {
        std::cerr << "[DISCOVERY] Bind failed: " << WSAGetLastError() << "\n";
        closesocket(sock);
        return false;
    }

    auto start = std::chrono::steady_clock::now();
    std::cout << "[DISCOVERY] Discovering server...\n";

    while (true) {
        const auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        if (elapsed >= timeout_seconds) {
            break;
        }

        fd_set read_set;
        FD_ZERO(&read_set);
        FD_SET(sock, &read_set);

        timeval tv{};
        tv.tv_sec = 0;
        tv.tv_usec = 500000;

        const int sel = select(0, &read_set, nullptr, nullptr, &tv);
        if (sel > 0 && FD_ISSET(sock, &read_set)) {
            char buffer[1024] = {0};
            sockaddr_in src{};
            int src_len = sizeof(src);
            const int recv_len = recvfrom(sock, buffer, sizeof(buffer) - 1, 0, reinterpret_cast<sockaddr*>(&src), &src_len);
            if (recv_len > 0) {
                const std::string payload(buffer, buffer + recv_len);
                if (payload == "VOICE_SERVER") {
                    char ip_str[INET_ADDRSTRLEN] = {0};
                    inet_ntop(AF_INET, &src.sin_addr, ip_str, sizeof(ip_str));
                    server_ip_ = ip_str;
                    std::cout << "[DISCOVERY] Server found: " << server_ip_ << "\n";
                    break;
                }
            }
        } else {
            const int broadcast = 1;
            setsockopt(sock, SOL_SOCKET, SO_BROADCAST, reinterpret_cast<const char*>(&broadcast), sizeof(broadcast));

            sockaddr_in bcast{};
            bcast.sin_family = AF_INET;
            bcast.sin_port = htons(static_cast<u_short>(DISCOVERY_PORT));
            bcast.sin_addr.s_addr = htonl(INADDR_BROADCAST);
            const char* probe = "VOICE_DISCOVER";
            sendto(sock, probe, static_cast<int>(strlen(probe)), 0, reinterpret_cast<sockaddr*>(&bcast), sizeof(bcast));

            std::vector<std::string> gateways = {
                "192.168.1.1",
                "192.168.0.1",
                "10.0.0.1",
                "192.168.1.255",
                "192.168.0.255",
            };
            for (const auto& gw : gateways) {
                sockaddr_in gw_addr{};
                gw_addr.sin_family = AF_INET;
                gw_addr.sin_port = htons(static_cast<u_short>(DISCOVERY_PORT));
                inet_pton(AF_INET, gw.c_str(), &gw_addr.sin_addr);
                sendto(sock, probe, static_cast<int>(strlen(probe)), 0, reinterpret_cast<sockaddr*>(&gw_addr), sizeof(gw_addr));
            }
        }
    }

    closesocket(sock);
    if (server_ip_.empty()) {
        std::cout << "[DISCOVERY] Server discovery timed out - will prompt for manual IP\n";
    }
    return !server_ip_.empty();
}
