#ifndef NETWORK_DISCOVERY_H
#define NETWORK_DISCOVERY_H

#include <string>

class NetworkDiscovery {
public:
    bool discover(double timeout_seconds = 10.0);
    const std::string& server_ip() const { return server_ip_; }

private:
    std::string server_ip_;
};

#endif
