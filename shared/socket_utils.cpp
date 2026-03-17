#include "socket_utils.h"

#include <ws2tcpip.h>

namespace socket_utils {

void set_dscp(SOCKET sock, int ip_tos) {
    if (sock == INVALID_SOCKET) {
        return;
    }
    const int tos = ip_tos;
    setsockopt(sock, IPPROTO_IP, IP_TOS, reinterpret_cast<const char*>(&tos), sizeof(tos));
}

}
