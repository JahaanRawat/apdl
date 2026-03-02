#pragma once
#include "crow.h"
#include "apdl/sse/broadcaster.h"
#include "apdl/store/postgres.h"
#include "apdl/store/redis_cache.h"

namespace apdl {

// Handle SSE stream connection for real-time flag updates.
// This endpoint keeps the connection open and pushes events as flags change.
crow::response handle_sse_stream(const crow::request& req,
                                  SSEBroadcaster& broadcaster,
                                  PostgresStore& pg,
                                  RedisCache& cache);

}
