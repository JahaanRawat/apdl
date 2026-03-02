#pragma once
#include <string>
#include <memory>
#include <mutex>
#include <hiredis/hiredis.h>

namespace apdl {

class RedisProducer {
public:
    static std::unique_ptr<RedisProducer> create(const std::string& url);
    ~RedisProducer();

    RedisProducer(const RedisProducer&) = delete;
    RedisProducer& operator=(const RedisProducer&) = delete;

    bool publish(const std::string& stream_key, const std::string& event_json);
    bool ping();

private:
    RedisProducer(redisContext* ctx, const std::string& host, int port);
    bool reconnect();

    redisContext* ctx_;
    std::string host_;
    int port_;
    std::mutex mutex_;
};

}
