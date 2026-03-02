#pragma once
#include <string>
#include <vector>
#include <unordered_map>
#include <mutex>
#include <functional>
#include <thread>
#include <atomic>

namespace apdl {

using SSEWriter = std::function<void(const std::string&)>;

struct SSEConnection {
    std::string connection_id;
    std::string project_id;
    SSEWriter writer;
    std::string last_event_id;
};

class SSEBroadcaster {
public:
    SSEBroadcaster();
    ~SSEBroadcaster();

    SSEBroadcaster(const SSEBroadcaster&) = delete;
    SSEBroadcaster& operator=(const SSEBroadcaster&) = delete;

    void start();
    void stop();

    std::string addConnection(const std::string& project_id, SSEWriter writer, const std::string& last_event_id = "");
    void removeConnection(const std::string& project_id, const std::string& connection_id);
    void broadcast(const std::string& project_id, const std::string& event_type, const std::string& data);
    size_t connectionCount(const std::string& project_id) const;
    size_t totalConnectionCount() const;

private:
    void heartbeatLoop();
    std::string generateConnectionId();

    std::unordered_map<std::string, std::vector<SSEConnection>> connections_;
    mutable std::mutex mutex_;
    std::thread heartbeat_thread_;
    std::atomic<bool> running_{false};
    std::atomic<uint64_t> event_counter_{0};
    std::atomic<uint64_t> conn_counter_{0};
};

}
