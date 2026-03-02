#include "apdl/middleware/rate_limit.h"
#include "spdlog/spdlog.h"

namespace apdl {

static constexpr double DEFAULT_CAPACITY = 1000.0;
static constexpr double DEFAULT_RATE = 100.0; // tokens per second

void RateLimitMiddleware::before_handle(crow::request& req, crow::response& res, context& ctx) {
    // Skip rate limiting for health checks
    if (req.url == "/health") {
        return;
    }

    // Use the project_id from the API key for per-project rate limiting.
    // Extract it from the X-API-Key header the same way auth middleware does.
    std::string bucket_key;

    std::string api_key = req.get_header_value("X-API-Key");
    if (api_key.empty()) {
        auto url_params = crow::query_string(req.url_params);
        const char* key_param = url_params.get("api_key");
        if (key_param) {
            api_key = key_param;
        }
    }

    // Parse project_id from key
    if (api_key.size() > 5 && api_key.substr(0, 5) == "proj_") {
        auto second_underscore = api_key.find('_', 5);
        if (second_underscore != std::string::npos) {
            bucket_key = "project:" + api_key.substr(5, second_underscore - 5);
        }
    }

    // Fall back to IP-based rate limiting if no project_id
    if (bucket_key.empty()) {
        bucket_key = "ip:" + req.remote_ip_address;
    }

    std::lock_guard<std::mutex> lock(mutex_);

    auto now = std::chrono::steady_clock::now();
    auto it = buckets_.find(bucket_key);

    if (it == buckets_.end()) {
        // Create a new bucket with full capacity
        TokenBucket bucket;
        bucket.tokens = DEFAULT_CAPACITY - 1.0; // consume one token
        bucket.last_refill = now;
        bucket.rate = DEFAULT_RATE;
        bucket.capacity = DEFAULT_CAPACITY;
        buckets_[bucket_key] = bucket;
        return;
    }

    TokenBucket& bucket = it->second;

    // Refill tokens based on elapsed time
    auto elapsed = std::chrono::duration<double>(now - bucket.last_refill).count();
    bucket.tokens = std::min(bucket.capacity, bucket.tokens + elapsed * bucket.rate);
    bucket.last_refill = now;

    // Try to consume one token
    if (bucket.tokens < 1.0) {
        ctx.rate_limited = true;

        // Calculate retry-after in seconds
        double deficit = 1.0 - bucket.tokens;
        int retry_after = static_cast<int>(std::ceil(deficit / bucket.rate));
        if (retry_after < 1) retry_after = 1;

        spdlog::warn("Rate limit exceeded for {}", bucket_key);

        res.code = 429;
        res.set_header("Content-Type", "application/json");
        res.set_header("Retry-After", std::to_string(retry_after));
        res.set_header("X-RateLimit-Limit", std::to_string(static_cast<int>(bucket.capacity)));
        res.set_header("X-RateLimit-Remaining", "0");
        res.body = R"({"error":"rate_limited","message":"Too many requests. Please retry after the Retry-After period."})";
        res.end();
        return;
    }

    bucket.tokens -= 1.0;

    // Set rate limit headers on all responses
    res.set_header("X-RateLimit-Limit", std::to_string(static_cast<int>(bucket.capacity)));
    res.set_header("X-RateLimit-Remaining", std::to_string(static_cast<int>(bucket.tokens)));
}

void RateLimitMiddleware::after_handle(crow::request& /*req*/, crow::response& /*res*/, context& /*ctx*/) {
    // No post-processing needed
}

} // namespace apdl
