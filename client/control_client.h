#ifndef CONTROL_CLIENT_H
#define CONTROL_CLIENT_H

#include <string>
#include <vector>

struct ControlResponse {
    bool ok;
    std::string response;
};

ControlResponse send_control_command(const std::string& server_ip, const std::string& command, int timeout_ms = 5000);
std::vector<std::string> parse_client_list_response(const std::string& response);

#endif
