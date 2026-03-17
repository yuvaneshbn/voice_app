#include "voice_server.h"

#include "../shared/opus_codec.h"
#include "../shared/socket_utils.h"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <wincrypt.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstring>
#include <chrono>
#include <deque>
#include <functional>
#include <iostream>
#include <mutex>
#include <optional>
#include <queue>
#include <sstream>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace {
constexpr int DISCOVERY_PORT = 50000;
constexpr int CONTROL_PORT = 50001;
constexpr int AUDIO_PORT = 50002;
constexpr const char* DEFAULT_ROOM = "main";
constexpr const char* MULTICAST_BASE = "239.0.0.";
constexpr int CLIENT_TIMEOUT_SEC = 35;
constexpr int DSCP_EF = 46;
constexpr int DSCP_CS3 = 24;
constexpr int IP_TOS_EF = DSCP_EF << 2;
constexpr int IP_TOS_CS3 = DSCP_CS3 << 2;
constexpr int MIX_FRAME_SAMPLES = 320;
constexpr int MIX_FRAME_BYTES = MIX_FRAME_SAMPLES * 2;
constexpr double ROOM_SOURCE_STALE_SEC = 0.25;
constexpr int ROOM_MIX_QUEUE_MAX = 128;
constexpr int VAD_PEAK_THRESHOLD = 120;

std::string normalize_client_id(const std::string& id) {
    std::string out = id;
    while (!out.empty() && out.back() == ',') {
        out.pop_back();
    }
    return out;
}

bool is_valid_client_id(const std::string& id) {
    if (id.empty()) {
        return false;
    }
    if (id.find(':') != std::string::npos || id.find('|') != std::string::npos) {
        return false;
    }
    if (id.find('\n') != std::string::npos || id.find('\r') != std::string::npos || id.find('\t') != std::string::npos) {
        return false;
    }
    if (id.find(',') != std::string::npos) {
        return false;
    }
    return true;
}

std::string multicast_addr_for_room(const std::string& room_id) {
    const std::string room = room_id.empty() ? DEFAULT_ROOM : room_id;
    int value = 1;
    HCRYPTPROV provider = 0;
    HCRYPTHASH hash = 0;
    BYTE digest[16] = {0};
    DWORD digest_len = sizeof(digest);
    if (CryptAcquireContextA(&provider, nullptr, nullptr, PROV_RSA_FULL, CRYPT_VERIFYCONTEXT) &&
        CryptCreateHash(provider, CALG_MD5, 0, 0, &hash) &&
        CryptHashData(hash,
                      reinterpret_cast<const BYTE*>(room.data()),
                      static_cast<DWORD>(room.size()),
                      0) &&
        CryptGetHashParam(hash, HP_HASHVAL, digest, &digest_len, 0) &&
        digest_len >= sizeof(digest)) {
        unsigned long long tail = 0;
        for (int i = 0; i < 8; ++i) {
            tail = (tail << 8) | digest[8 + i];
        }
        value = static_cast<int>(tail % 255ULL) + 1;
    }
    if (hash) {
        CryptDestroyHash(hash);
    }
    if (provider) {
        CryptReleaseContext(provider, 0);
    }
    std::ostringstream oss;
    oss << MULTICAST_BASE << value;
    return oss.str();
}

struct Client {
    std::string client_id;
    sockaddr_in addr{};
    std::string room;
    std::unordered_set<std::string> targets;
    std::optional<std::unordered_set<std::string>> hear_targets;
    std::chrono::steady_clock::time_point last_heartbeat;
};

struct MixFrame {
    uint16_t seq;
    std::vector<std::pair<std::string, std::vector<int16_t>>> sources;
};

class RoomMixer {
public:
    explicit RoomMixer(const std::string& room_id)
        : room_id_(room_id) {
        running_.store(true);
        mix_thread_ = std::thread(&RoomMixer::mixLoop, this);
    }

    ~RoomMixer() {
        stop();
    }

    void addPcm(const std::string& sender_id, const std::vector<int16_t>& pcm) {
        if (pcm.empty()) {
            return;
        }
        int peak = 0;
        for (int i = 0; i < std::min(static_cast<int>(pcm.size()), MIX_FRAME_SAMPLES); ++i) {
            peak = std::max(peak, static_cast<int>(std::abs(pcm[i])));
        }
        if (peak < VAD_PEAK_THRESHOLD) {
            std::lock_guard<std::mutex> lock(mutex_);
            sources_.erase(sender_id);
            last_seen_.erase(sender_id);
            return;
        }
        std::lock_guard<std::mutex> lock(mutex_);
        sources_[sender_id] = pcm;
        last_seen_[sender_id] = std::chrono::steady_clock::now();
    }

    void removeSource(const std::string& sender_id) {
        std::lock_guard<std::mutex> lock(mutex_);
        sources_.erase(sender_id);
        last_seen_.erase(sender_id);
    }

    std::shared_ptr<OpusCodec> getOrCreateListenerEncoder(const std::string& listener_id) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = listener_encoders_.find(listener_id);
        if (it != listener_encoders_.end()) {
            return it->second;
        }
        auto encoder = std::make_shared<OpusCodec>(16000, 1, MIX_FRAME_SAMPLES, true, 15, 48000, 10, false, OPUS_APPLICATION_AUDIO, true, false);
        listener_encoders_[listener_id] = encoder;
        return encoder;
    }

    void removeListener(const std::string& listener_id) {
        std::lock_guard<std::mutex> lock(mutex_);
        listener_encoders_.erase(listener_id);
    }

    std::vector<MixFrame> drainPackets(int limit) {
        std::vector<MixFrame> packets;
        std::lock_guard<std::mutex> lock(queue_mutex_);
        for (int i = 0; i < limit && !mix_queue_.empty(); ++i) {
            packets.push_back(std::move(mix_queue_.front()));
            mix_queue_.pop_front();
        }
        return packets;
    }

    void stop() {
        running_.store(false);
        if (mix_thread_.joinable()) {
            mix_thread_.join();
        }
    }

private:
    void mixLoop() {
        const double frame_period = 0.020;
        while (running_.load()) {
            auto start = std::chrono::steady_clock::now();
            std::vector<std::pair<std::string, std::vector<int16_t>>> source_items;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                pruneStaleLocked();
                for (const auto& entry : sources_) {
                    source_items.push_back(entry);
                }
            }
            if (!source_items.empty()) {
                enqueueMixFrame(std::move(source_items));
            }
            auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            const double sleep_for = std::max(0.0, frame_period - elapsed);
            if (sleep_for > 0.0) {
                std::this_thread::sleep_for(std::chrono::duration<double>(sleep_for));
            }
        }
    }

    void pruneStaleLocked() {
        const auto now = std::chrono::steady_clock::now();
        for (auto it = last_seen_.begin(); it != last_seen_.end();) {
            const double age = std::chrono::duration<double>(now - it->second).count();
            if (age > ROOM_SOURCE_STALE_SEC) {
                sources_.erase(it->first);
                it = last_seen_.erase(it);
            } else {
                ++it;
            }
        }
    }

    void enqueueMixFrame(std::vector<std::pair<std::string, std::vector<int16_t>>> source_items) {
        MixFrame frame;
        frame.seq = seq_++;
        frame.sources = std::move(source_items);

        std::lock_guard<std::mutex> lock(queue_mutex_);
        if (mix_queue_.size() >= ROOM_MIX_QUEUE_MAX) {
            mix_queue_.pop_front();
        }
        mix_queue_.push_back(std::move(frame));
    }

public:
    static std::vector<int16_t> mixPcmFrames(const std::vector<std::vector<int16_t>>& frames) {
        std::vector<int> mix(MIX_FRAME_SAMPLES, 0);
        for (const auto& frame : frames) {
            for (int i = 0; i < MIX_FRAME_SAMPLES; ++i) {
                int sample = 0;
                if (i < static_cast<int>(frame.size())) {
                    sample = frame[i];
                }
                mix[i] += sample;
            }
        }

        const int active = static_cast<int>(frames.size());
        float gain = 1.0f;
        if (active == 2) {
            gain = 0.95f;
        } else if (active > 2) {
            gain = 2.5f / static_cast<float>(active);
        }

        std::vector<int16_t> out(MIX_FRAME_SAMPLES, 0);
        for (int i = 0; i < MIX_FRAME_SAMPLES; ++i) {
            const int scaled = static_cast<int>(mix[i] * gain);
            const int clamped = std::max(-32768, std::min(32767, scaled));
            out[i] = static_cast<int16_t>(clamped);
        }
        return out;
    }

private:
    std::string room_id_;
    std::atomic<bool> running_{false};
    std::thread mix_thread_;

    std::mutex mutex_;
    std::unordered_map<std::string, std::vector<int16_t>> sources_;
    std::unordered_map<std::string, std::chrono::steady_clock::time_point> last_seen_;
    std::unordered_map<std::string, std::shared_ptr<OpusCodec>> listener_encoders_;

    std::mutex queue_mutex_;
    std::deque<MixFrame> mix_queue_;
    uint16_t seq_ = 0;
};

} // namespace
class VoiceServer::Impl {
public:
    Impl() {
        const char* secret_env = std::getenv("VOICE_REGISTER_SECRET");
        server_secret_ = secret_env ? secret_env : "mysecret";
    }

    bool start() {
        running_.store(true);

        udp_sock_ = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (udp_sock_ == INVALID_SOCKET) {
            return false;
        }
        const int reuse = 1;
        setsockopt(udp_sock_, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
        const int buf_size = 1024 * 1024;
        setsockopt(udp_sock_, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<const char*>(&buf_size), sizeof(buf_size));
        setsockopt(udp_sock_, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const char*>(&buf_size), sizeof(buf_size));
        socket_utils::set_dscp(udp_sock_, IP_TOS_EF);

        sockaddr_in udp_addr{};
        udp_addr.sin_family = AF_INET;
        udp_addr.sin_port = htons(static_cast<u_short>(AUDIO_PORT));
        udp_addr.sin_addr.s_addr = htonl(INADDR_ANY);
        if (bind(udp_sock_, reinterpret_cast<sockaddr*>(&udp_addr), sizeof(udp_addr)) == SOCKET_ERROR) {
            closesocket(udp_sock_);
            udp_sock_ = INVALID_SOCKET;
            return false;
        }

        tcp_sock_ = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (tcp_sock_ == INVALID_SOCKET) {
            closesocket(udp_sock_);
            udp_sock_ = INVALID_SOCKET;
            return false;
        }
        setsockopt(tcp_sock_, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
        sockaddr_in tcp_addr{};
        tcp_addr.sin_family = AF_INET;
        tcp_addr.sin_port = htons(static_cast<u_short>(CONTROL_PORT));
        tcp_addr.sin_addr.s_addr = htonl(INADDR_ANY);
        if (bind(tcp_sock_, reinterpret_cast<sockaddr*>(&tcp_addr), sizeof(tcp_addr)) == SOCKET_ERROR) {
            closesocket(tcp_sock_);
            closesocket(udp_sock_);
            tcp_sock_ = INVALID_SOCKET;
            udp_sock_ = INVALID_SOCKET;
            return false;
        }
        if (listen(tcp_sock_, SOMAXCONN) == SOCKET_ERROR) {
            closesocket(tcp_sock_);
            closesocket(udp_sock_);
            tcp_sock_ = INVALID_SOCKET;
            udp_sock_ = INVALID_SOCKET;
            return false;
        }

        std::cout << "[SERVER] Audio UDP listening on port " << AUDIO_PORT << "\n";
        std::cout << "[SERVER] Control TCP listening on port " << CONTROL_PORT << "\n";

        discovery_thread_ = std::thread(&Impl::broadcastLoop, this);
        control_thread_ = std::thread(&Impl::controlAcceptLoop, this);
        audio_thread_ = std::thread(&Impl::audioReceiveLoop, this);
        send_thread_ = std::thread(&Impl::sendMixLoop, this);
        prune_thread_ = std::thread(&Impl::pruneLoop, this);

        return true;
    }

    void stop() {
        running_.store(false);
        if (tcp_sock_ != INVALID_SOCKET) {
            closesocket(tcp_sock_);
            tcp_sock_ = INVALID_SOCKET;
        }
        if (udp_sock_ != INVALID_SOCKET) {
            closesocket(udp_sock_);
            udp_sock_ = INVALID_SOCKET;
        }

        if (discovery_thread_.joinable()) {
            discovery_thread_.join();
        }
        if (control_thread_.joinable()) {
            control_thread_.join();
        }
        if (audio_thread_.joinable()) {
            audio_thread_.join();
        }
        if (send_thread_.joinable()) {
            send_thread_.join();
        }
        if (prune_thread_.joinable()) {
            prune_thread_.join();
        }

        std::vector<std::shared_ptr<RoomMixer>> mixers;
        {
            std::lock_guard<std::mutex> lock(state_mutex_);
            for (auto& entry : room_mixers_) {
                mixers.push_back(entry.second);
            }
            room_mixers_.clear();
            clients_.clear();
            rooms_.clear();
            sender_decoders_.clear();
        }
        for (auto& mixer : mixers) {
            mixer->stop();
        }

        std::cout << "[SERVER] Shutdown complete. UDP/TCP ports released.\n";
    }

    bool isRunning() const { return running_.load(); }

private:
    void broadcastLoop() {
        SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (sock == INVALID_SOCKET) {
            return;
        }
        const int broadcast = 1;
        setsockopt(sock, SOL_SOCKET, SO_BROADCAST, reinterpret_cast<const char*>(&broadcast), sizeof(broadcast));
        socket_utils::set_dscp(sock, IP_TOS_CS3);

        sockaddr_in bcast{};
        bcast.sin_family = AF_INET;
        bcast.sin_port = htons(static_cast<u_short>(DISCOVERY_PORT));
        bcast.sin_addr.s_addr = htonl(INADDR_BROADCAST);

        while (running_.load()) {
            const char* msg = "VOICE_SERVER";
            sendto(sock, msg, static_cast<int>(strlen(msg)), 0, reinterpret_cast<sockaddr*>(&bcast), sizeof(bcast));
            for (int i = 0; i < 20 && running_.load(); ++i) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        }
        closesocket(sock);
    }
    void controlAcceptLoop() {
        while (running_.load()) {
            sockaddr_in client_addr{};
            int addr_len = sizeof(client_addr);
            SOCKET client_sock = accept(tcp_sock_, reinterpret_cast<sockaddr*>(&client_addr), &addr_len);
            if (client_sock == INVALID_SOCKET) {
                if (!running_.load()) {
                    break;
                }
                continue;
            }
            std::thread(&Impl::handleControlConnection, this, client_sock, client_addr).detach();
        }
    }

    void handleControlConnection(SOCKET client_sock, sockaddr_in client_addr) {
        socket_utils::set_dscp(client_sock, IP_TOS_CS3);
        const int nodelay = 1;
        setsockopt(client_sock, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&nodelay), sizeof(nodelay));

        char buffer[1024] = {0};
        const int recv_len = recv(client_sock, buffer, sizeof(buffer) - 1, 0);
        if (recv_len <= 0) {
            closesocket(client_sock);
            return;
        }
        std::string message(buffer, buffer + recv_len);
        message.erase(std::remove(message.begin(), message.end(), '\r'), message.end());
        message.erase(std::remove(message.begin(), message.end(), '\n'), message.end());

        const std::string peer_ip = inet_ntoa(client_addr.sin_addr);

        std::string response = "ERR\n";

        auto parts = splitMessage(message);
        const std::string cmd = parts.empty() ? "" : parts[0];
        const std::string client_id = parts.size() > 1 ? parts[1] : "";

        if (cmd == "REGISTER" && validateRegister(parts)) {
            if (parts.size() < 3 || !is_valid_client_id(client_id)) {
                response = "ERR\n";
            } else {
                int audio_port = 0;
                try {
                    audio_port = std::stoi(parts[2]);
                } catch (...) {
                    audio_port = 0;
                }
                if (audio_port <= 0) {
                    response = "ERR\n";
                } else {
                    bool taken = false;
                    {
                        std::lock_guard<std::mutex> lock(state_mutex_);
                        taken = clients_.find(client_id) != clients_.end();
                        if (!taken) {
                            Client client;
                            client.client_id = client_id;
                            client.addr = client_addr;
                            client.addr.sin_port = htons(static_cast<u_short>(audio_port));
                            client.last_heartbeat = std::chrono::steady_clock::now();
                            clients_[client_id] = client;
                        }
                    }
                    if (taken) {
                        response = "TAKEN\n";
                        std::cout << "[SERVER] Client " << client_id << " already in use\n";
                    } else {
                        response = "OK\n";
                        std::cout << "[SERVER] " << client_id << " registered from " << peer_ip << ":" << audio_port << "\n";
                    }
                }
            }
        } else if (cmd == "LIST") {
            std::vector<std::string> entries;
            {
                std::lock_guard<std::mutex> lock(state_mutex_);
                if (!client_id.empty() && clients_.find(client_id) != clients_.end()) {
                    const std::string room_id = clients_[client_id].room;
                    if (!room_id.empty()) {
                        for (const auto& cid : rooms_[room_id]) {
                            entries.push_back(cid);
                        }
                    }
                } else {
                    for (const auto& entry : clients_) {
                        entries.push_back(entry.first);
                    }
                }
            }
            std::sort(entries.begin(), entries.end());
            std::string body;
            for (const auto& entry : entries) {
                body += entry + "\n";
            }
            response = body.empty() ? "\n" : body;
        } else if (cmd == "PING") {
            std::lock_guard<std::mutex> lock(state_mutex_);
            auto it = clients_.find(client_id);
            if (it != clients_.end()) {
                it->second.last_heartbeat = std::chrono::steady_clock::now();
                response = "OK\n";
            }
        } else if (cmd == "JOIN" && parts.size() == 3) {
            if (clients_.find(client_id) != clients_.end()) {
                const std::string room_id = parts[2].empty() ? DEFAULT_ROOM : parts[2];
                joinRoom(client_id, room_id);
                response = "OK:" + multicast_addr_for_room(room_id) + "\n";
            }
        } else if (cmd == "TARGETS" || cmd == "TALK") {
            const std::string targets_str = parts.size() > 2 ? parts[2] : "";
            std::lock_guard<std::mutex> lock(state_mutex_);
            auto it = clients_.find(client_id);
            if (it != clients_.end()) {
                it->second.targets = resolveTargetsLocked(client_id, targets_str);
                it->second.last_heartbeat = std::chrono::steady_clock::now();
                response = "OK\n";
            }
        } else if (cmd == "HEAR") {
            const std::string hear_str = parts.size() > 2 ? parts[2] : "";
            std::lock_guard<std::mutex> lock(state_mutex_);
            auto it = clients_.find(client_id);
            if (it != clients_.end()) {
                it->second.hear_targets = resolveTargetsLocked(client_id, hear_str);
                it->second.last_heartbeat = std::chrono::steady_clock::now();
                response = "OK\n";
            }
        } else if (cmd == "UNREGISTER") {
            if (clients_.find(client_id) != clients_.end()) {
                removeClient(client_id);
                response = "OK\n";
            }
        }

        send(client_sock, response.c_str(), static_cast<int>(response.size()), 0);
        closesocket(client_sock);
    }

    std::vector<std::string> splitMessage(const std::string& message) {
        std::vector<std::string> parts;
        std::string token;
        std::istringstream iss(message);
        while (std::getline(iss, token, ':')) {
            parts.push_back(token);
        }
        return parts;
    }

    bool validateRegister(const std::vector<std::string>& parts) const {
        if (parts.size() == 3) {
            return true;
        }
        if (parts.size() == 4) {
            return parts[3] == server_secret_;
        }
        return false;
    }

    std::unordered_set<std::string> resolveTargetsLocked(const std::string& sender_id, const std::string& targets_str) {
        std::unordered_set<std::string> resolved;
        auto sender_it = clients_.find(sender_id);
        if (sender_it == clients_.end() || sender_it->second.room.empty()) {
            return resolved;
        }
        const std::string room_id = sender_it->second.room;
        const auto& room_members = rooms_[room_id];

        std::unordered_map<std::string, std::vector<std::string>> normalized;
        for (const auto& member : room_members) {
            normalized[normalize_client_id(member)].push_back(member);
        }

        std::istringstream iss(targets_str);
        std::string token;
        while (std::getline(iss, token, ',')) {
            token.erase(std::remove_if(token.begin(), token.end(), ::isspace), token.end());
            if (token.empty()) {
                continue;
            }
            if (token == sender_id) {
                continue;
            }
            if (room_members.find(token) != room_members.end()) {
                resolved.insert(token);
                continue;
            }
            const std::string norm = normalize_client_id(token);
            auto it = normalized.find(norm);
            if (it != normalized.end()) {
                for (const auto& candidate : it->second) {
                    if (candidate != sender_id) {
                        resolved.insert(candidate);
                        break;
                    }
                }
            }
        }
        return resolved;
    }

    void joinRoom(const std::string& client_id, const std::string& room_id) {
        std::shared_ptr<RoomMixer> old_mixer;
        std::shared_ptr<RoomMixer> stale_mixer;
        {
            std::lock_guard<std::mutex> lock(state_mutex_);
            auto it = clients_.find(client_id);
            if (it == clients_.end()) {
                return;
            }
            std::string old_room = it->second.room;
            if (old_room == room_id && rooms_[room_id].find(client_id) != rooms_[room_id].end()) {
                it->second.last_heartbeat = std::chrono::steady_clock::now();
                getOrCreateMixer(room_id);
                return;
            }
            if (!old_room.empty() && old_room != room_id) {
                old_mixer = room_mixers_[old_room];
                rooms_[old_room].erase(client_id);
                if (rooms_[old_room].empty()) {
                    rooms_.erase(old_room);
                    stale_mixer = room_mixers_[old_room];
                    room_mixers_.erase(old_room);
                }
            }
            it->second.room = room_id;
            it->second.last_heartbeat = std::chrono::steady_clock::now();
            rooms_[room_id].insert(client_id);
        }

        if (old_mixer) {
            old_mixer->removeListener(client_id);
        }
        if (stale_mixer) {
            stale_mixer->stop();
        }
        getOrCreateMixer(room_id);
        std::cout << "[SERVER] " << client_id << " joined room " << room_id << "\n";
    }

    void removeClient(const std::string& client_id) {
        std::shared_ptr<RoomMixer> mixer_to_stop;
        {
            std::lock_guard<std::mutex> lock(state_mutex_);
            auto it = clients_.find(client_id);
            if (it == clients_.end()) {
                return;
            }
            if (!it->second.room.empty()) {
                const std::string room_id = it->second.room;
                rooms_[room_id].erase(client_id);
                if (rooms_[room_id].empty()) {
                    rooms_.erase(room_id);
                    mixer_to_stop = room_mixers_[room_id];
                    room_mixers_.erase(room_id);
                }
                auto mixer_it = room_mixers_.find(room_id);
                if (mixer_it != room_mixers_.end()) {
                    mixer_it->second->removeSource(client_id);
                    mixer_it->second->removeListener(client_id);
                }
            }
            sender_decoders_.erase(client_id);
            clients_.erase(it);
        }
        if (mixer_to_stop) {
            mixer_to_stop->stop();
        }
        std::cout << "[SERVER] " << client_id << " disconnected\n";
    }

    std::shared_ptr<RoomMixer> getOrCreateMixer(const std::string& room_id) {
        auto it = room_mixers_.find(room_id);
        if (it != room_mixers_.end()) {
            return it->second;
        }
        auto mixer = std::make_shared<RoomMixer>(room_id);
        room_mixers_[room_id] = mixer;
        return mixer;
    }
    void audioReceiveLoop() {
        while (running_.load()) {
            std::vector<uint8_t> buffer(4096);
            sockaddr_in src{};
            int src_len = sizeof(src);
            const int recv_len = recvfrom(udp_sock_, reinterpret_cast<char*>(buffer.data()), static_cast<int>(buffer.size()), 0, reinterpret_cast<sockaddr*>(&src), &src_len);
            if (recv_len <= 0) {
                if (!running_.load()) {
                    break;
                }
                continue;
            }
            buffer.resize(static_cast<size_t>(recv_len));
            processAudioPacket(buffer, src);
        }
    }

    void processAudioPacket(const std::vector<uint8_t>& packet, const sockaddr_in& addr) {
        const std::string sender_id = extractSenderId(packet);
        if (sender_id.empty()) {
            return;
        }

        std::string room_id;
        bool can_mix = false;
        {
            std::lock_guard<std::mutex> lock(state_mutex_);
            auto it = clients_.find(sender_id);
            if (it == clients_.end()) {
                return;
            }
            it->second.last_heartbeat = std::chrono::steady_clock::now();
            room_id = it->second.room;
            can_mix = senderHasLiveTargetsLocked(it->second);
        }
        if (room_id.empty() || !can_mix) {
            return;
        }

        const std::vector<uint8_t> opus_payload = extractOpusPayload(packet);
        if (opus_payload.empty()) {
            return;
        }

        std::shared_ptr<OpusCodec> decoder = getOrCreateDecoder(sender_id);
        std::vector<int16_t> pcm;
        try {
            pcm = decoder->decode(opus_payload);
        } catch (...) {
            return;
        }
        if (pcm.empty()) {
            return;
        }
        if (pcm.size() < MIX_FRAME_SAMPLES) {
            pcm.resize(MIX_FRAME_SAMPLES, 0);
        } else if (pcm.size() > MIX_FRAME_SAMPLES) {
            pcm.resize(MIX_FRAME_SAMPLES);
        }

        auto mixer = getOrCreateMixer(room_id);
        mixer->addPcm(sender_id, pcm);
    }

    std::string extractSenderId(const std::vector<uint8_t>& packet) {
        if (packet.empty()) {
            return {};
        }
        auto it = std::find(packet.begin(), packet.end(), ':');
        if (it == packet.end()) {
            return {};
        }
        std::string header(packet.begin(), it);
        auto pipe = header.find('|');
        if (pipe == std::string::npos) {
            return {};
        }
        std::string sender = header.substr(0, pipe);
        return sender;
    }

    std::vector<uint8_t> extractOpusPayload(const std::vector<uint8_t>& packet) {
        auto it = std::find(packet.begin(), packet.end(), ':');
        if (it == packet.end()) {
            return {};
        }
        return std::vector<uint8_t>(it + 1, packet.end());
    }

    std::shared_ptr<OpusCodec> getOrCreateDecoder(const std::string& sender_id) {
        std::lock_guard<std::mutex> lock(state_mutex_);
        auto it = sender_decoders_.find(sender_id);
        if (it != sender_decoders_.end()) {
            return it->second;
        }
        auto decoder = std::make_shared<OpusCodec>(16000, 1, MIX_FRAME_SAMPLES, true, 15, 48000, 10, false, OPUS_APPLICATION_AUDIO, false, true);
        sender_decoders_[sender_id] = decoder;
        return decoder;
    }

    bool senderHasLiveTargetsLocked(const Client& sender) {
        if (sender.targets.empty()) {
            return false;
        }
        if (sender.room.empty()) {
            return false;
        }
        auto room_it = rooms_.find(sender.room);
        if (room_it == rooms_.end()) {
            return false;
        }
        for (const auto& target : sender.targets) {
            if (room_it->second.find(target) != room_it->second.end() && target != sender.client_id) {
                return true;
            }
        }
        return false;
    }

    void sendMixLoop() {
        while (running_.load()) {
            std::vector<std::pair<std::shared_ptr<RoomMixer>, std::vector<std::tuple<std::string, sockaddr_in, std::optional<std::unordered_set<std::string>>>>>> room_entries;
            {
                std::lock_guard<std::mutex> lock(state_mutex_);
                for (const auto& room_entry : room_mixers_) {
                    const std::string& room_id = room_entry.first;
                    auto mixer = room_entry.second;
                    std::vector<std::tuple<std::string, sockaddr_in, std::optional<std::unordered_set<std::string>>>> listeners;
                    auto members_it = rooms_.find(room_id);
                    if (members_it != rooms_.end()) {
                        for (const auto& client_id : members_it->second) {
                            auto client_it = clients_.find(client_id);
                            if (client_it != clients_.end()) {
                                listeners.emplace_back(client_id, client_it->second.addr, client_it->second.hear_targets);
                            }
                        }
                    }
                    room_entries.emplace_back(mixer, listeners);
                }
            }

            for (auto& entry : room_entries) {
                auto mixer = entry.first;
                auto& listeners = entry.second;
                if (!mixer || listeners.empty()) {
                    continue;
                }
                auto mix_frames = mixer->drainPackets(64);
                if (mix_frames.empty()) {
                    continue;
                }
                for (const auto& frame : mix_frames) {
                    std::vector<std::string> active_sender_ids;
                    for (const auto& src : frame.sources) {
                        active_sender_ids.push_back(src.first);
                    }
                    for (const auto& listener : listeners) {
                        const std::string& listener_id = std::get<0>(listener);
                        const sockaddr_in& addr = std::get<1>(listener);
                        const auto& hear_targets = std::get<2>(listener);

                        std::vector<std::string> active_others;
                        for (const auto& sid : active_sender_ids) {
                            if (sid != listener_id) {
                                active_others.push_back(sid);
                            }
                        }
                        if (active_others.empty()) {
                            continue;
                        }
                        if (hear_targets.has_value()) {
                            bool invalid = false;
                            for (const auto& sid : active_others) {
                                if (hear_targets->find(sid) == hear_targets->end()) {
                                    invalid = true;
                                    break;
                                }
                            }
                            if (invalid) {
                                continue;
                            }
                        }

                        std::vector<std::vector<int16_t>> frames;
                        for (const auto& src : frame.sources) {
                            if (src.first != listener_id) {
                                frames.push_back(src.second);
                            }
                        }
                        if (frames.empty()) {
                            continue;
                        }
                        auto mixed_pcm = RoomMixer::mixPcmFrames(frames);
                        auto encoder = mixer->getOrCreateListenerEncoder(listener_id);
                        std::vector<uint8_t> opus;
                        try {
                            opus = encoder->encode(mixed_pcm);
                        } catch (...) {
                            continue;
                        }
                        if (opus.empty()) {
                            continue;
                        }
                        std::string header = "MIXED|" + std::to_string(frame.seq) + "|";
                        std::vector<uint8_t> packet;
                        packet.reserve(header.size() + opus.size());
                        packet.insert(packet.end(), header.begin(), header.end());
                        packet.insert(packet.end(), opus.begin(), opus.end());
                        sendto(udp_sock_, reinterpret_cast<const char*>(packet.data()), static_cast<int>(packet.size()), 0, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr));
                    }
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
    }

    void pruneLoop() {
        while (running_.load()) {
            pruneStaleClients();
            for (int i = 0; i < 50 && running_.load(); ++i) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        }
    }

    void pruneStaleClients() {
        std::vector<std::string> stale;
        const auto now = std::chrono::steady_clock::now();
        {
            std::lock_guard<std::mutex> lock(state_mutex_);
            for (const auto& entry : clients_) {
                const double age = std::chrono::duration<double>(now - entry.second.last_heartbeat).count();
                if (age > CLIENT_TIMEOUT_SEC) {
                    stale.push_back(entry.first);
                }
            }
        }
        for (const auto& client_id : stale) {
            removeClient(client_id);
        }
    }

private:
    std::atomic<bool> running_{false};
    SOCKET udp_sock_ = INVALID_SOCKET;
    SOCKET tcp_sock_ = INVALID_SOCKET;

    std::thread discovery_thread_;
    std::thread control_thread_;
    std::thread audio_thread_;
    std::thread send_thread_;
    std::thread prune_thread_;

    std::mutex state_mutex_;
    std::unordered_map<std::string, Client> clients_;
    std::unordered_map<std::string, std::unordered_set<std::string>> rooms_;
    std::unordered_map<std::string, std::shared_ptr<RoomMixer>> room_mixers_;
    std::unordered_map<std::string, std::shared_ptr<OpusCodec>> sender_decoders_;

    std::string server_secret_;
};

VoiceServer::VoiceServer() : impl_(std::make_unique<Impl>()) {}

VoiceServer::~VoiceServer() {
    stop();
}

bool VoiceServer::start() {
    if (running_.load()) {
        return true;
    }
    if (!impl_->start()) {
        return false;
    }
    running_.store(true);
    return true;
}

void VoiceServer::stop() {
    if (!running_.load()) {
        return;
    }
    impl_->stop();
    running_.store(false);
}
