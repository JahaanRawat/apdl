#pragma once
#include "crow.h"
#include <unordered_map>
#include <mutex>
#include <chrono>

namespace apdl {

struct TokenBucket {
    double tokens;
    std::chrono::steady_clock::time_point last_refill;
    double rate;       // tokens per second
    double capacity;
};

struct RateLimitMiddleware {
    struct context {
        bool rate_limited = false;
    };

    void before_handle(crow::request& req, crow::response& res, context& ctx);
    void after_handle(crow::request& req, crow::response& res, context& ctx);

private:
    std::unordered_map<std::string, TokenBucket> buckets_;
    std::mutex mutex_;
};

}
