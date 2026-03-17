#include "winsock_init.h"

WinSockInit::WinSockInit() {
    const int rc = WSAStartup(MAKEWORD(2, 2), &wsa_);
    ok_ = (rc == 0);
}

WinSockInit::~WinSockInit() {
    if (ok_) {
        WSACleanup();
    }
}
