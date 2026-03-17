#ifndef SOCKET_UTILS_H
#define SOCKET_UTILS_H

#include <winsock2.h>

namespace socket_utils {

void set_dscp(SOCKET sock, int ip_tos);

}

#endif
