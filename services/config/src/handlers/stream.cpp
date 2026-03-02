#include "apdl/handlers/stream.h"
#include "spdlog/spdlog.h"
#include "rapidjson/document.h"
#include "rapidjson/writer.h"
#include "rapidjson/stringbuffer.h"

namespace apdl {

static std::string extract_project_id(const crow::request& req) {
    std::string api_key = req.get_header_value("X-API-Key");
    if (api_key.empty()) {
        auto url_params = crow::query_string(req.url_params);
        const char* key_param = url_params.get("api_key");
        if (key_param) api_key = key_param;
    }

    if (api_key.size() > 5 && api_key.substr(0, 5) == "proj_") {
        auto second_underscore = api_key.find('_', 5);
        if (second_underscore != std::string::npos) {
            return api_key.substr(5, second_underscore - 5);
        }
    }

    auto url_params = crow::query_string(req.url_params);
    const char* pid = url_params.get("project_id");
    if (pid) return std::string(pid);

    return "";
}

static std::string flags_to_json_array(const std::vector<FlagConfig>& flags) {
    rapidjson::StringBuffer buf;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buf);

    writer.StartArray();
    for (const auto& f : flags) {
        writer.StartObject();
        writer.Key("key");
        writer.String(f.key.c_str());
        writer.Key("enabled");
        writer.Bool(f.enabled);
        writer.Key("variant_type");
        writer.String(f.variant_type.c_str());
        writer.Key("default_value");
        writer.RawValue(f.default_value.c_str(), f.default_value.size(), rapidjson::kStringType);
        writer.Key("rollout_percentage");
        writer.Double(f.rollout_percentage);

        if (!f.rules_json.empty() && f.rules_json != "[]") {
            writer.Key("rules");
            writer.RawValue(f.rules_json.c_str(), f.rules_json.size(), rapidjson::kArrayType);
        }

        if (!f.variants_json.empty() && f.variants_json != "[]") {
            writer.Key("variants");
            writer.RawValue(f.variants_json.c_str(), f.variants_json.size(), rapidjson::kArrayType);
        }

        writer.Key("updated_at");
        writer.String(f.updated_at.c_str());
        writer.EndObject();
    }
    writer.EndArray();

    return std::string(buf.GetString(), buf.GetSize());
}

crow::response handle_sse_stream(const crow::request& req,
                                  SSEBroadcaster& broadcaster,
                                  PostgresStore& pg,
                                  RedisCache& cache) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) {
        crow::json::wvalue err;
        err["error"] = "unauthorized";
        err["message"] = "API key or project_id required for SSE stream";
        return crow::response(401, err);
    }

    std::string last_event_id = req.get_header_value("Last-Event-ID");

    // Build the initial config snapshot
    auto flags = pg.getFlags(project_id);
    std::string initial_data = flags_to_json_array(flags);

    // Format the SSE response. Crow does not natively support streaming
    // responses with chunked transfer encoding, so we build the initial
    // payload as an SSE-formatted response body. The broadcaster will
    // handle subsequent pushes via its registered writer (in a real
    // production system, this would use Crow's streaming response or
    // a dedicated WebSocket/SSE library).
    //
    // For this implementation, we return the initial config as an SSE-formatted
    // response and register the connection with the broadcaster for future
    // updates. The client is expected to reconnect when the response ends.

    // Build SSE formatted response
    std::string sse_body;
    sse_body += "event: config\n";
    sse_body += "data: " + initial_data + "\n";
    sse_body += "\n";

    // Register connection with broadcaster for future pushes.
    // In a full implementation with Crow streaming, the writer would push
    // directly to the chunked response. Here we log the registration.
    std::string conn_id = broadcaster.addConnection(
        project_id,
        [](const std::string& /*data*/) {
            // In a full streaming implementation, this callback would write
            // to the chunked response output. With Crow's current API,
            // real-time push requires WebSocket or an async response adapter.
        },
        last_event_id
    );

    spdlog::info("SSE connection {} registered for project {} (total: {})",
                 conn_id, project_id, broadcaster.connectionCount(project_id));

    crow::response res(200, sse_body);
    res.set_header("Content-Type", "text/event-stream");
    res.set_header("Cache-Control", "no-cache");
    res.set_header("Connection", "keep-alive");
    res.set_header("X-Accel-Buffering", "no");
    res.set_header("Access-Control-Allow-Origin", "*");

    return res;
}

} // namespace apdl
