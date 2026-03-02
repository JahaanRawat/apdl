#pragma once
#include "crow.h"
#include "apdl/store/postgres.h"
#include "apdl/store/redis_cache.h"
#include "apdl/sse/broadcaster.h"

namespace apdl {

// Flag admin CRUD
crow::response admin_create_flag(const crow::request& req,
                                  PostgresStore& pg,
                                  RedisCache& cache,
                                  SSEBroadcaster& broadcaster);

crow::response admin_update_flag(const crow::request& req,
                                  const std::string& flag_key,
                                  PostgresStore& pg,
                                  RedisCache& cache,
                                  SSEBroadcaster& broadcaster);

crow::response admin_delete_flag(const crow::request& req,
                                  const std::string& flag_key,
                                  PostgresStore& pg,
                                  RedisCache& cache,
                                  SSEBroadcaster& broadcaster);

crow::response admin_list_flags(const crow::request& req,
                                 PostgresStore& pg);

// Experiment admin CRUD
crow::response admin_create_experiment(const crow::request& req,
                                        PostgresStore& pg,
                                        RedisCache& cache,
                                        SSEBroadcaster& broadcaster);

crow::response admin_update_experiment(const crow::request& req,
                                        const std::string& experiment_key,
                                        PostgresStore& pg,
                                        RedisCache& cache,
                                        SSEBroadcaster& broadcaster);

crow::response admin_delete_experiment(const crow::request& req,
                                        const std::string& experiment_key,
                                        PostgresStore& pg,
                                        RedisCache& cache,
                                        SSEBroadcaster& broadcaster);

crow::response admin_list_experiments(const crow::request& req,
                                       PostgresStore& pg);

}
