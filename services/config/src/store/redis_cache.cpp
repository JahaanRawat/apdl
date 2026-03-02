#include "apdl/store/redis_cache.h"
#include "spdlog/spdlog.h"

#include <regex>
#include <cstring>

namespace apdl {

static const std::string FLAGS_PREFIX = "config:flags:";
static const std::string EXPERIMENTS_PREFIX = "config:experiments:";

RedisCache::RedisCache(redisContext* ctx, const std::string& host, int port)
    : ctx_(ctx), host_(host), port_(port) {}

RedisCache::~RedisCache() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (ctx_) {
        redisFree(ctx_);
        ctx_ = nullptr;
    }
}

std::unique_ptr<RedisCache> RedisCache::create(const std::string& url) {
    std::string host = "localhost";
    int port = 6379;

    std::regex url_regex(R"(^(?:redis://)?([^:/?#]+)(?::(\d+))?)");
    std::smatch match;

    if (std::regex_search(url, match, url_regex)) {
        if (match[1].matched && match[1].length() > 0) {
            host = match[1].str();
        }
        if (match[2].matched && match[2].length() > 0) {
            try {
                port = std::stoi(match[2].str());
            } catch (...) {
                port = 6379;
            }
        }
    }

    struct timeval timeout = {2, 0};
    redisContext* ctx = redisConnectWithTimeout(host.c_str(), port, timeout);

    if (!ctx) {
        spdlog::error("Failed to allocate Redis context for cache");
        return nullptr;
    }

    if (ctx->err) {
        spdlog::error("Redis cache connection error: {}", ctx->errstr);
        redisFree(ctx);
        return nullptr;
    }

    struct timeval cmd_timeout = {1, 0};
    redisSetTimeout(ctx, cmd_timeout);

    return std::unique_ptr<RedisCache>(new RedisCache(ctx, host, port));
}

bool RedisCache::reconnect() {
    spdlog::warn("Attempting Redis cache reconnection to {}:{}", host_, port_);

    if (ctx_) {
        redisFree(ctx_);
        ctx_ = nullptr;
    }

    struct timeval timeout = {2, 0};
    ctx_ = redisConnectWithTimeout(host_.c_str(), port_, timeout);

    if (!ctx_) {
        spdlog::error("Redis cache reconnect: failed to allocate context");
        return false;
    }

    if (ctx_->err) {
        spdlog::error("Redis cache reconnect failed: {}", ctx_->errstr);
        redisFree(ctx_);
        ctx_ = nullptr;
        return false;
    }

    struct timeval cmd_timeout = {1, 0};
    redisSetTimeout(ctx_, cmd_timeout);

    spdlog::info("Redis cache reconnection successful");
    return true;
}

// ---- Generic operations ----

std::optional<std::string> RedisCache::get(const std::string& key) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!ctx_) {
        if (!reconnect()) return std::nullopt;
    }

    redisReply* reply = static_cast<redisReply*>(
        redisCommand(ctx_, "GET %b", key.c_str(), key.size()));

    if (!reply) {
        spdlog::warn("Redis GET failed, connection lost");
        reconnect();
        return std::nullopt;
    }

    std::optional<std::string> result;

    if (reply->type == REDIS_REPLY_STRING && reply->str) {
        result = std::string(reply->str, reply->len);
    }
    // REDIS_REPLY_NIL means key not found, which is a valid "no result"

    freeReplyObject(reply);
    return result;
}

bool RedisCache::set(const std::string& key, const std::string& value, int ttl_seconds) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!ctx_) {
        if (!reconnect()) return false;
    }

    redisReply* reply = static_cast<redisReply*>(
        redisCommand(ctx_, "SET %b %b EX %d",
                     key.c_str(), key.size(),
                     value.c_str(), value.size(),
                     ttl_seconds));

    if (!reply) {
        spdlog::warn("Redis SET failed, connection lost");
        reconnect();
        return false;
    }

    bool ok = (reply->type == REDIS_REPLY_STATUS &&
               std::strcmp(reply->str, "OK") == 0);

    freeReplyObject(reply);
    return ok;
}

bool RedisCache::del(const std::string& key) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!ctx_) {
        if (!reconnect()) return false;
    }

    redisReply* reply = static_cast<redisReply*>(
        redisCommand(ctx_, "DEL %b", key.c_str(), key.size()));

    if (!reply) {
        spdlog::warn("Redis DEL failed, connection lost");
        reconnect();
        return false;
    }

    bool ok = (reply->type == REDIS_REPLY_INTEGER);
    freeReplyObject(reply);
    return ok;
}

// ---- Flag cache operations ----

std::optional<std::string> RedisCache::getFlags(const std::string& project_id) {
    return get(FLAGS_PREFIX + project_id);
}

bool RedisCache::setFlags(const std::string& project_id, const std::string& flags_json, int ttl_seconds) {
    return set(FLAGS_PREFIX + project_id, flags_json, ttl_seconds);
}

bool RedisCache::invalidateFlags(const std::string& project_id) {
    spdlog::debug("Invalidating flags cache for project {}", project_id);
    return del(FLAGS_PREFIX + project_id);
}

// ---- Experiment cache operations ----

std::optional<std::string> RedisCache::getExperiments(const std::string& project_id) {
    return get(EXPERIMENTS_PREFIX + project_id);
}

bool RedisCache::setExperiments(const std::string& project_id, const std::string& json, int ttl_seconds) {
    return set(EXPERIMENTS_PREFIX + project_id, json, ttl_seconds);
}

bool RedisCache::invalidateExperiments(const std::string& project_id) {
    spdlog::debug("Invalidating experiments cache for project {}", project_id);
    return del(EXPERIMENTS_PREFIX + project_id);
}

// ---- Health ----

bool RedisCache::ping() {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!ctx_) {
        return reconnect();
    }

    redisReply* reply = static_cast<redisReply*>(redisCommand(ctx_, "PING"));
    if (!reply) {
        spdlog::warn("Redis cache PING failed, attempting reconnect");
        return reconnect();
    }

    bool ok = (reply->type == REDIS_REPLY_STATUS &&
               std::strcmp(reply->str, "PONG") == 0);

    freeReplyObject(reply);
    return ok;
}

} // namespace apdl
