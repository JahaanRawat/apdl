#include "apdl/middleware/auth.h"
#include "spdlog/spdlog.h"

#include <regex>

namespace apdl {

// API key format: proj_{project_id}_{secret}
// project_id: alphanumeric, 1-64 chars
// secret: alphanumeric, 16+ chars
static const std::regex KEY_PATTERN(R"(^proj_([a-zA-Z0-9]{1,64})_([a-zA-Z0-9]{16,})$)");

void AuthMiddleware::before_handle(crow::request& req, crow::response& res, context& ctx) {
    // Skip auth for health checks
    if (req.url == "/health") {
        ctx.authenticated = true;
        return;
    }

    // Extract API key from header or query parameter
    std::string api_key = req.get_header_value("X-API-Key");

    if (api_key.empty()) {
        // Try query parameter
        auto url_params = crow::query_string(req.url_params);
        const char* key_param = url_params.get("api_key");
        if (key_param) {
            api_key = key_param;
        }
    }

    if (api_key.empty()) {
        spdlog::debug("Request rejected: no API key provided");
        res.code = 401;
        res.set_header("Content-Type", "application/json");
        res.body = R"({"error":"unauthorized","message":"API key required. Pass X-API-Key header or api_key query parameter."})";
        res.end();
        return;
    }

    // Validate key format
    std::smatch match;
    if (!std::regex_match(api_key, match, KEY_PATTERN)) {
        spdlog::debug("Request rejected: malformed API key");
        res.code = 401;
        res.set_header("Content-Type", "application/json");
        res.body = R"({"error":"unauthorized","message":"Invalid API key format. Expected: proj_{project_id}_{secret}"})";
        res.end();
        return;
    }

    ctx.api_key = api_key;
    ctx.project_id = match[1].str();
    ctx.authenticated = true;

    spdlog::debug("Authenticated request for project {}", ctx.project_id);
}

void AuthMiddleware::after_handle(crow::request& /*req*/, crow::response& /*res*/, context& /*ctx*/) {
    // No post-processing needed
}

} // namespace apdl
