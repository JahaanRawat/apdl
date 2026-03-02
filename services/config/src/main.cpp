#include "crow.h"
#include "spdlog/spdlog.h"
#include "apdl/handlers/flags.h"
#include "apdl/handlers/stream.h"
#include "apdl/handlers/admin.h"
#include "apdl/store/postgres.h"
#include "apdl/store/redis_cache.h"
#include "apdl/sse/broadcaster.h"

#include <cstdlib>
#include <string>
#include <memory>
#include <csignal>

static std::string env_or(const char* name, const std::string& fallback) {
    const char* val = std::getenv(name);
    return val ? std::string(val) : fallback;
}

int main() {
    spdlog::set_level(spdlog::level::info);
    spdlog::set_pattern("[%Y-%m-%d %H:%M:%S.%e] [%l] [%t] %v");

    const std::string redis_url = env_or("REDIS_URL", "redis://localhost:6379");
    const std::string postgres_url = env_or("POSTGRES_URL",
        "postgresql://localhost:5432/apdl?user=apdl&password=apdl");
    const int port = std::stoi(env_or("PORT", "8081"));
    const int threads = std::stoi(env_or("THREADS", "4"));
    const int pg_pool_size = std::stoi(env_or("PG_POOL_SIZE", "4"));

    // Connect to PostgreSQL
    spdlog::info("Connecting to PostgreSQL");
    auto pg = apdl::PostgresStore::create(postgres_url, pg_pool_size);
    if (!pg) {
        spdlog::critical("Failed to connect to PostgreSQL");
        return 1;
    }
    if (!pg->ping()) {
        spdlog::critical("PostgreSQL ping failed");
        return 1;
    }
    spdlog::info("PostgreSQL connection pool established (size={})", pg_pool_size);

    // Initialize schema
    if (!pg->initSchema()) {
        spdlog::critical("Failed to initialize database schema");
        return 1;
    }
    spdlog::info("Database schema initialized");

    // Connect to Redis
    spdlog::info("Connecting to Redis at {}", redis_url);
    auto cache = apdl::RedisCache::create(redis_url);
    if (!cache) {
        spdlog::critical("Failed to connect to Redis at {}", redis_url);
        return 1;
    }
    if (!cache->ping()) {
        spdlog::critical("Redis ping failed");
        return 1;
    }
    spdlog::info("Redis cache connection established");

    // Start SSE broadcaster
    auto broadcaster = std::make_shared<apdl::SSEBroadcaster>();
    broadcaster->start();
    spdlog::info("SSE broadcaster started");

    // Use shared_ptr for lambda captures with Crow's copy semantics
    auto pg_ptr = std::shared_ptr<apdl::PostgresStore>(std::move(pg));
    auto cache_ptr = std::shared_ptr<apdl::RedisCache>(std::move(cache));

    crow::SimpleApp app;

    // ---- Client-facing endpoints ----

    // GET /v1/flags - Get all flags for a project (SDK polling endpoint)
    CROW_ROUTE(app, "/v1/flags").methods(crow::HTTPMethod::GET)(
        [pg_ptr, cache_ptr](const crow::request& req) {
            return apdl::handle_get_flags(req, *pg_ptr, *cache_ptr);
        });

    // GET /v1/stream - SSE stream for real-time flag updates
    CROW_ROUTE(app, "/v1/stream").methods(crow::HTTPMethod::GET)(
        [broadcaster, pg_ptr, cache_ptr](const crow::request& req) {
            return apdl::handle_sse_stream(req, *broadcaster, *pg_ptr, *cache_ptr);
        });

    // ---- Admin endpoints ----

    // Flags CRUD
    CROW_ROUTE(app, "/v1/admin/flags").methods(crow::HTTPMethod::GET)(
        [pg_ptr](const crow::request& req) {
            return apdl::admin_list_flags(req, *pg_ptr);
        });

    CROW_ROUTE(app, "/v1/admin/flags").methods(crow::HTTPMethod::POST)(
        [pg_ptr, cache_ptr, broadcaster](const crow::request& req) {
            return apdl::admin_create_flag(req, *pg_ptr, *cache_ptr, *broadcaster);
        });

    CROW_ROUTE(app, "/v1/admin/flags/<string>").methods(crow::HTTPMethod::PUT)(
        [pg_ptr, cache_ptr, broadcaster](const crow::request& req, const std::string& key) {
            return apdl::admin_update_flag(req, key, *pg_ptr, *cache_ptr, *broadcaster);
        });

    CROW_ROUTE(app, "/v1/admin/flags/<string>").methods(crow::HTTPMethod::DELETE)(
        [pg_ptr, cache_ptr, broadcaster](const crow::request& req, const std::string& key) {
            return apdl::admin_delete_flag(req, key, *pg_ptr, *cache_ptr, *broadcaster);
        });

    // Experiments CRUD
    CROW_ROUTE(app, "/v1/admin/experiments").methods(crow::HTTPMethod::GET)(
        [pg_ptr](const crow::request& req) {
            return apdl::admin_list_experiments(req, *pg_ptr);
        });

    CROW_ROUTE(app, "/v1/admin/experiments").methods(crow::HTTPMethod::POST)(
        [pg_ptr, cache_ptr, broadcaster](const crow::request& req) {
            return apdl::admin_create_experiment(req, *pg_ptr, *cache_ptr, *broadcaster);
        });

    CROW_ROUTE(app, "/v1/admin/experiments/<string>").methods(crow::HTTPMethod::PUT)(
        [pg_ptr, cache_ptr, broadcaster](const crow::request& req, const std::string& key) {
            return apdl::admin_update_experiment(req, key, *pg_ptr, *cache_ptr, *broadcaster);
        });

    CROW_ROUTE(app, "/v1/admin/experiments/<string>").methods(crow::HTTPMethod::DELETE)(
        [pg_ptr, cache_ptr, broadcaster](const crow::request& req, const std::string& key) {
            return apdl::admin_delete_experiment(req, key, *pg_ptr, *cache_ptr, *broadcaster);
        });

    // Health check
    CROW_ROUTE(app, "/health").methods(crow::HTTPMethod::GET)(
        [pg_ptr, cache_ptr, broadcaster]() {
            bool pg_ok = pg_ptr->ping();
            bool redis_ok = cache_ptr->ping();
            size_t sse_conns = broadcaster->totalConnectionCount();

            crow::json::wvalue body;
            body["status"] = (pg_ok && redis_ok) ? "ok" : "degraded";
            body["service"] = "config";
            body["postgres"] = pg_ok ? "ok" : "error";
            body["redis"] = redis_ok ? "ok" : "error";
            body["sse_connections"] = sse_conns;

            return crow::response((pg_ok && redis_ok) ? 200 : 503, body);
        });

    spdlog::info("Starting APDL config service on port {} with {} threads", port, threads);

    app.port(port)
       .multithreaded()
       .concurrency(threads)
       .run();

    broadcaster->stop();
    spdlog::info("Config service shut down");

    return 0;
}
