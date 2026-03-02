#include "crow.h"
#include "spdlog/spdlog.h"
#include "apdl/handlers/events.h"
#include "apdl/streaming/redis_producer.h"
#include "apdl/middleware/auth.h"
#include "apdl/middleware/rate_limit.h"

#include <cstdlib>
#include <string>
#include <memory>

static std::string env_or(const char* name, const std::string& fallback) {
    const char* val = std::getenv(name);
    return val ? std::string(val) : fallback;
}

int main() {
    spdlog::set_level(spdlog::level::info);
    spdlog::set_pattern("[%Y-%m-%d %H:%M:%S.%e] [%l] [%t] %v");

    const std::string redis_url = env_or("REDIS_URL", "redis://localhost:6379");
    const int port = std::stoi(env_or("PORT", "8080"));
    const int threads = std::stoi(env_or("THREADS", "4"));

    spdlog::info("Connecting to Redis at {}", redis_url);
    auto redis = apdl::RedisProducer::create(redis_url);
    if (!redis) {
        spdlog::critical("Failed to connect to Redis at {}", redis_url);
        return 1;
    }

    if (!redis->ping()) {
        spdlog::critical("Redis ping failed");
        return 1;
    }
    spdlog::info("Redis connection established");

    // Shared pointer so the lambda captures work with Crow's copy semantics
    auto redis_ptr = std::shared_ptr<apdl::RedisProducer>(std::move(redis));

    crow::App<apdl::AuthMiddleware, apdl::RateLimitMiddleware> app;

    CROW_ROUTE(app, "/v1/events").methods(crow::HTTPMethod::POST)(
        [redis_ptr](const crow::request& req) {
            return apdl::handle_events(req, *redis_ptr);
        });

    CROW_ROUTE(app, "/health").methods(crow::HTTPMethod::GET)(
        [redis_ptr]() {
            bool healthy = redis_ptr->ping();
            crow::json::wvalue body;
            body["status"] = healthy ? "ok" : "degraded";
            body["service"] = "ingestion";
            return crow::response(healthy ? 200 : 503, body);
        });

    spdlog::info("Starting APDL ingestion service on port {} with {} threads", port, threads);

    app.port(port)
       .multithreaded()
       .concurrency(threads)
       .run();

    return 0;
}
