#pragma once
#include "crow.h"
#include "apdl/streaming/redis_producer.h"

namespace apdl {
crow::response handle_events(const crow::request& req, RedisProducer& redis);
}
