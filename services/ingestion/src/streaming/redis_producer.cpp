#include "apdl/streaming/redis_producer.h"
#include "spdlog/spdlog.h"

#include <regex>
#include <cstring>

namespace apdl {

// Maximum stream length for XADD MAXLEN trimming (approximate)
static constexpr int64_t STREAM_MAXLEN = 1000000;

RedisProducer::RedisProducer(redisContext* ctx, const std::string& host, int port)
    : ctx_(ctx), host_(host), port_(port) {}

RedisProducer::~RedisProducer() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (ctx_) {
        redisFree(ctx_);
        ctx_ = nullptr;
    }
}

std::unique_ptr<RedisProducer> RedisProducer::create(const std::string& url) {
    // Parse redis://host:port or redis://host or host:port
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

    spdlog::debug("Connecting to Redis at {}:{}", host, port);

    struct timeval timeout = {2, 0}; // 2 second connect timeout
    redisContext* ctx = redisConnectWithTimeout(host.c_str(), port, timeout);

    if (!ctx) {
        spdlog::error("Failed to allocate Redis context");
        return nullptr;
    }

    if (ctx->err) {
        spdlog::error("Redis connection error: {}", ctx->errstr);
        redisFree(ctx);
        return nullptr;
    }

    // Set a command timeout of 1 second
    struct timeval cmd_timeout = {1, 0};
    redisSetTimeout(ctx, cmd_timeout);

    return std::unique_ptr<RedisProducer>(new RedisProducer(ctx, host, port));
}

bool RedisProducer::reconnect() {
    spdlog::warn("Attempting Redis reconnection to {}:{}", host_, port_);

    if (ctx_) {
        redisFree(ctx_);
        ctx_ = nullptr;
    }

    struct timeval timeout = {2, 0};
    ctx_ = redisConnectWithTimeout(host_.c_str(), port_, timeout);

    if (!ctx_) {
        spdlog::error("Redis reconnect: failed to allocate context");
        return false;
    }

    if (ctx_->err) {
        spdlog::error("Redis reconnect failed: {}", ctx_->errstr);
        redisFree(ctx_);
        ctx_ = nullptr;
        return false;
    }

    struct timeval cmd_timeout = {1, 0};
    redisSetTimeout(ctx_, cmd_timeout);

    spdlog::info("Redis reconnection successful");
    return true;
}

bool RedisProducer::publish(const std::string& stream_key, const std::string& event_json) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!ctx_) {
        if (!reconnect()) {
            return false;
        }
    }

    // XADD stream_key MAXLEN ~ 1000000 * event event_json
    redisReply* reply = static_cast<redisReply*>(redisCommand(
        ctx_,
        "XADD %s MAXLEN ~ %lld * event %b",
        stream_key.c_str(),
        static_cast<long long>(STREAM_MAXLEN),
        event_json.c_str(),
        event_json.size()
    ));

    if (!reply) {
        spdlog::warn("Redis XADD returned null reply, connection may be lost: {}",
                      ctx_->errstr ? ctx_->errstr : "unknown error");
        // Try reconnect and retry once
        if (!reconnect()) {
            return false;
        }

        reply = static_cast<redisReply*>(redisCommand(
            ctx_,
            "XADD %s MAXLEN ~ %lld * event %b",
            stream_key.c_str(),
            static_cast<long long>(STREAM_MAXLEN),
            event_json.c_str(),
            event_json.size()
        ));

        if (!reply) {
            spdlog::error("Redis XADD failed after reconnect");
            return false;
        }
    }

    if (reply->type == REDIS_REPLY_ERROR) {
        spdlog::error("Redis XADD error: {}", reply->str ? reply->str : "unknown");
        freeReplyObject(reply);
        return false;
    }

    freeReplyObject(reply);
    return true;
}

bool RedisProducer::ping() {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!ctx_) {
        return reconnect();
    }

    redisReply* reply = static_cast<redisReply*>(redisCommand(ctx_, "PING"));
    if (!reply) {
        spdlog::warn("Redis PING failed, attempting reconnect");
        return reconnect();
    }

    bool ok = (reply->type == REDIS_REPLY_STATUS &&
               std::strcmp(reply->str, "PONG") == 0);

    freeReplyObject(reply);

    if (!ok) {
        spdlog::warn("Redis PING returned unexpected reply");
    }

    return ok;
}

} // namespace apdl
