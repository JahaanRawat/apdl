#include "apdl/sse/broadcaster.h"
#include "spdlog/spdlog.h"

#include <algorithm>
#include <sstream>

namespace apdl {

static constexpr int HEARTBEAT_INTERVAL_SECONDS = 30;

SSEBroadcaster::SSEBroadcaster() = default;

SSEBroadcaster::~SSEBroadcaster() {
    stop();
}

void SSEBroadcaster::start() {
    bool expected = false;
    if (!running_.compare_exchange_strong(expected, true)) {
        return; // Already running
    }

    heartbeat_thread_ = std::thread(&SSEBroadcaster::heartbeatLoop, this);
    spdlog::info("SSE broadcaster heartbeat thread started");
}

void SSEBroadcaster::stop() {
    bool expected = true;
    if (!running_.compare_exchange_strong(expected, false)) {
        return; // Not running
    }

    if (heartbeat_thread_.joinable()) {
        heartbeat_thread_.join();
    }

    std::lock_guard<std::mutex> lock(mutex_);
    connections_.clear();
    spdlog::info("SSE broadcaster stopped, all connections cleared");
}

std::string SSEBroadcaster::generateConnectionId() {
    uint64_t id = conn_counter_.fetch_add(1, std::memory_order_relaxed);
    std::ostringstream oss;
    oss << "sse_" << id;
    return oss.str();
}

std::string SSEBroadcaster::addConnection(const std::string& project_id,
                                            SSEWriter writer,
                                            const std::string& last_event_id) {
    std::string conn_id = generateConnectionId();

    SSEConnection conn;
    conn.connection_id = conn_id;
    conn.project_id = project_id;
    conn.writer = std::move(writer);
    conn.last_event_id = last_event_id;

    std::lock_guard<std::mutex> lock(mutex_);
    connections_[project_id].push_back(std::move(conn));

    spdlog::debug("SSE connection {} added for project {} (total for project: {})",
                  conn_id, project_id, connections_[project_id].size());

    return conn_id;
}

void SSEBroadcaster::removeConnection(const std::string& project_id,
                                       const std::string& connection_id) {
    std::lock_guard<std::mutex> lock(mutex_);

    auto project_it = connections_.find(project_id);
    if (project_it == connections_.end()) return;

    auto& conns = project_it->second;
    conns.erase(
        std::remove_if(conns.begin(), conns.end(),
            [&connection_id](const SSEConnection& c) {
                return c.connection_id == connection_id;
            }),
        conns.end()
    );

    if (conns.empty()) {
        connections_.erase(project_it);
    }

    spdlog::debug("SSE connection {} removed for project {}", connection_id, project_id);
}

void SSEBroadcaster::broadcast(const std::string& project_id,
                                const std::string& event_type,
                                const std::string& data) {
    uint64_t event_id = event_counter_.fetch_add(1, std::memory_order_relaxed);

    // Format as SSE message
    std::ostringstream sse_msg;
    sse_msg << "id: " << event_id << "\n";
    sse_msg << "event: " << event_type << "\n";

    // Split data across lines if needed (SSE spec: each line prefixed with "data: ")
    std::istringstream data_stream(data);
    std::string line;
    while (std::getline(data_stream, line)) {
        sse_msg << "data: " << line << "\n";
    }
    sse_msg << "\n"; // Empty line terminates the event

    std::string message = sse_msg.str();

    std::lock_guard<std::mutex> lock(mutex_);

    auto it = connections_.find(project_id);
    if (it == connections_.end()) {
        spdlog::debug("No SSE connections for project {}, broadcast dropped", project_id);
        return;
    }

    std::vector<std::string> dead_connections;

    for (auto& conn : it->second) {
        try {
            conn.writer(message);
            conn.last_event_id = std::to_string(event_id);
        } catch (const std::exception& e) {
            spdlog::warn("SSE write failed for connection {}: {}", conn.connection_id, e.what());
            dead_connections.push_back(conn.connection_id);
        }
    }

    // Remove dead connections
    if (!dead_connections.empty()) {
        auto& conns = it->second;
        for (const auto& dead_id : dead_connections) {
            conns.erase(
                std::remove_if(conns.begin(), conns.end(),
                    [&dead_id](const SSEConnection& c) {
                        return c.connection_id == dead_id;
                    }),
                conns.end()
            );
        }
        if (conns.empty()) {
            connections_.erase(it);
        }
        spdlog::debug("Removed {} dead SSE connections for project {}",
                       dead_connections.size(), project_id);
    }

    spdlog::debug("Broadcast event {} ({}) to project {} ({} connections)",
                   event_id, event_type, project_id,
                   connections_.count(project_id) ? connections_[project_id].size() : 0);
}

size_t SSEBroadcaster::connectionCount(const std::string& project_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = connections_.find(project_id);
    if (it == connections_.end()) return 0;
    return it->second.size();
}

size_t SSEBroadcaster::totalConnectionCount() const {
    std::lock_guard<std::mutex> lock(mutex_);
    size_t total = 0;
    for (const auto& pair : connections_) {
        total += pair.second.size();
    }
    return total;
}

void SSEBroadcaster::heartbeatLoop() {
    while (running_.load(std::memory_order_relaxed)) {
        // Sleep in small increments so we can check running_ more frequently
        for (int i = 0; i < HEARTBEAT_INTERVAL_SECONDS * 10; ++i) {
            if (!running_.load(std::memory_order_relaxed)) return;
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }

        if (!running_.load(std::memory_order_relaxed)) return;

        // Send heartbeat comment to all connections
        // SSE comments start with ":" and are ignored by clients but keep
        // the connection alive through proxies and load balancers.
        const std::string heartbeat = ": heartbeat\n\n";

        std::lock_guard<std::mutex> lock(mutex_);

        for (auto project_it = connections_.begin(); project_it != connections_.end(); ) {
            std::vector<std::string> dead_connections;

            for (auto& conn : project_it->second) {
                try {
                    conn.writer(heartbeat);
                } catch (const std::exception& e) {
                    spdlog::debug("Heartbeat failed for connection {}: {}",
                                   conn.connection_id, e.what());
                    dead_connections.push_back(conn.connection_id);
                }
            }

            // Remove dead connections
            if (!dead_connections.empty()) {
                auto& conns = project_it->second;
                for (const auto& dead_id : dead_connections) {
                    conns.erase(
                        std::remove_if(conns.begin(), conns.end(),
                            [&dead_id](const SSEConnection& c) {
                                return c.connection_id == dead_id;
                            }),
                        conns.end()
                    );
                }
            }

            if (project_it->second.empty()) {
                project_it = connections_.erase(project_it);
            } else {
                ++project_it;
            }
        }
    }
}

} // namespace apdl
