#pragma once
#include "crow.h"
#include "apdl/store/postgres.h"
#include "apdl/store/redis_cache.h"

namespace apdl {
crow::response handle_get_flags(const crow::request& req, PostgresStore& pg, RedisCache& cache);
crow::response handle_create_flag(const crow::request& req, PostgresStore& pg, RedisCache& cache);
crow::response handle_update_flag(const crow::request& req, const std::string& flag_key, PostgresStore& pg, RedisCache& cache);
crow::response handle_delete_flag(const crow::request& req, const std::string& flag_key, PostgresStore& pg, RedisCache& cache);
}
