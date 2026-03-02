#pragma once
#include "crow.h"
#include <string>
#include <regex>

namespace apdl {

struct AuthMiddleware {
    struct context {
        std::string project_id;
        std::string api_key;
        bool authenticated = false;
    };

    void before_handle(crow::request& req, crow::response& res, context& ctx);
    void after_handle(crow::request& req, crow::response& res, context& ctx);
};

}
