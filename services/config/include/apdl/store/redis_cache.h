#pragma once
#include <string>
#include <optional>
#include <memory>
#include <mutex>
#include <hiredis/hiredis.h>

namespace apdl {

class RedisCache {
public:
    static std::unique_ptr<RedisCache> create(const std::string& url);
    ~RedisCache();

    RedisCache(const RedisCache&) = delete;
    RedisCache& operator=(const RedisCache&) = delete;

    // Get cached flags JSON for a project
    std::optional<std::string> getFlags(const std::string& project_id);

    // Cache flags JSON for a project with TTL
    bool setFlags(const std::string& project_id, const std::string& flags_json, int ttl_seconds = 60);

    // Invalidate cached flags for a project
    bool invalidateFlags(const std::string& project_id);

    // Get cached experiments JSON for a project
    std::optional<std::string> getExperiments(const std::string& project_id);

    // Cache experiments JSON
    bool setExperiments(const std::string& project_id, const std::string& json, int ttl_seconds = 60);

    // Invalidate cached experiments
    bool invalidateExperiments(const std::string& project_id);

    // Generic get/set
    std::optional<std::string> get(const std::string& key);
    bool set(const std::string& key, const std::string& value, int ttl_seconds = 60);
    bool del(const std::string& key);

    bool ping();

private:
    RedisCache(redisContext* ctx, const std::string& host, int port);
    bool reconnect();

    redisContext* ctx_;
    std::string host_;
    int port_;
    std::mutex mutex_;
};

}
