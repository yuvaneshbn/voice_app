#ifndef WINSOCK_INIT_H
#define WINSOCK_INIT_H

#include <winsock2.h>

class WinSockInit {
public:
    WinSockInit();
    ~WinSockInit();
    bool ok() const { return ok_; }

private:
    bool ok_ = false;
    WSADATA wsa_{};
};

#endif
