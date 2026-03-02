#include "apdl/handlers/events.h"
#include "apdl/validation/schema.h"
#include "apdl/util/json.h"
#include "apdl/middleware/auth.h"
#include "spdlog/spdlog.h"
#include "rapidjson/document.h"
#include "rapidjson/writer.h"
#include "rapidjson/stringbuffer.h"

#include <chrono>
#include <ctime>
#include <iomanip>
#include <sstream>

namespace apdl {

static std::string iso8601_now() {
    auto now = std::chrono::system_clock::now();
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()) % 1000;
    auto time_t_now = std::chrono::system_clock::to_time_t(now);

    std::tm utc_tm{};
#if defined(_WIN32)
    gmtime_s(&utc_tm, &time_t_now);
#else
    gmtime_r(&time_t_now, &utc_tm);
#endif

    std::ostringstream oss;
    oss << std::put_time(&utc_tm, "%Y-%m-%dT%H:%M:%S");
    oss << '.' << std::setfill('0') << std::setw(3) << ms.count() << 'Z';
    return oss.str();
}

static std::string extract_client_ip(const crow::request& req) {
    // Check common proxy headers first
    auto forwarded = req.get_header_value("X-Forwarded-For");
    if (!forwarded.empty()) {
        // X-Forwarded-For can be "client, proxy1, proxy2" -- take the first
        auto comma = forwarded.find(',');
        if (comma != std::string::npos) {
            return forwarded.substr(0, comma);
        }
        return forwarded;
    }

    auto real_ip = req.get_header_value("X-Real-IP");
    if (!real_ip.empty()) {
        return real_ip;
    }

    return req.remote_ip_address;
}

static std::string build_error_response(const ValidationResult& result) {
    rapidjson::StringBuffer buf;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buf);

    writer.StartObject();
    writer.Key("error");
    writer.String("validation_failed");
    writer.Key("errors");
    writer.StartArray();
    for (const auto& err : result.errors) {
        writer.StartObject();
        writer.Key("field");
        writer.String(err.field.c_str());
        writer.Key("message");
        writer.String(err.message.c_str());
        writer.EndObject();
    }
    writer.EndArray();
    writer.EndObject();

    return buf.GetString();
}

crow::response handle_events(const crow::request& req, RedisProducer& redis) {
    // Extract auth context from middleware. Crow stores middleware context in
    // the request's middleware_context which is templated. Since we call this
    // from a route handler that already verified auth via middleware, we rely
    // on the X-API-Key header being present and parse the project_id here
    // directly for simplicity (the middleware has already validated the key
    // format on the Crow middleware path).
    std::string project_id;
    std::string api_key = req.get_header_value("X-API-Key");
    if (api_key.empty()) {
        auto url_params = crow::query_string(req.url_params);
        const char* key_param = url_params.get("api_key");
        if (key_param) {
            api_key = key_param;
        }
    }

    // Parse project_id from key format "proj_{project_id}_{secret}"
    if (api_key.size() > 5 && api_key.substr(0, 5) == "proj_") {
        auto second_underscore = api_key.find('_', 5);
        if (second_underscore != std::string::npos) {
            project_id = api_key.substr(5, second_underscore - 5);
        }
    }

    if (project_id.empty()) {
        crow::json::wvalue err;
        err["error"] = "unauthorized";
        err["message"] = "Valid API key required. Format: proj_{project_id}_{secret}";
        return crow::response(401, err);
    }

    // Parse the request body
    if (req.body.empty()) {
        crow::json::wvalue err;
        err["error"] = "bad_request";
        err["message"] = "Request body is empty";
        return crow::response(400, err);
    }

    rapidjson::Document doc;
    doc.Parse(req.body.c_str(), req.body.size());

    if (doc.HasParseError()) {
        crow::json::wvalue err;
        err["error"] = "bad_request";
        err["message"] = "Invalid JSON in request body";
        return crow::response(400, err);
    }

    // Validate the batch
    auto validation = validate_event_batch(doc);
    if (!validation.valid) {
        auto body = build_error_response(validation);
        crow::response res(400, body);
        res.set_header("Content-Type", "application/json");
        return res;
    }

    const auto& events = doc["events"].GetArray();
    const std::string server_ts = iso8601_now();
    const std::string client_ip = extract_client_ip(req);
    const std::string stream_key = "events:raw:" + project_id;

    int accepted = 0;
    int failed = 0;

    for (rapidjson::SizeType i = 0; i < events.Size(); ++i) {
        // Build enriched event JSON. We copy the original event and add
        // server-side fields to avoid mutating the parsed doc.
        rapidjson::Document enriched;
        enriched.CopyFrom(events[i], enriched.GetAllocator());

        // Add server-side enrichment fields
        rapidjson::Value ts_val;
        ts_val.SetString(server_ts.c_str(), static_cast<rapidjson::SizeType>(server_ts.size()),
                         enriched.GetAllocator());
        enriched.AddMember("server_timestamp", ts_val, enriched.GetAllocator());

        rapidjson::Value ip_val;
        ip_val.SetString(client_ip.c_str(), static_cast<rapidjson::SizeType>(client_ip.size()),
                         enriched.GetAllocator());
        enriched.AddMember("ip", ip_val, enriched.GetAllocator());

        rapidjson::Value pid_val;
        pid_val.SetString(project_id.c_str(), static_cast<rapidjson::SizeType>(project_id.size()),
                          enriched.GetAllocator());
        enriched.AddMember("project_id", pid_val, enriched.GetAllocator());

        // Serialize the enriched event
        std::string event_json = serialize(enriched);

        if (redis.publish(stream_key, event_json)) {
            ++accepted;
        } else {
            ++failed;
            spdlog::warn("Failed to publish event {} to Redis stream {}", i, stream_key);
        }
    }

    if (accepted == 0 && failed > 0) {
        crow::json::wvalue err;
        err["error"] = "service_unavailable";
        err["message"] = "Failed to enqueue events to processing pipeline";
        return crow::response(503, err);
    }

    crow::json::wvalue body;
    body["accepted"] = accepted;
    if (failed > 0) {
        body["failed"] = failed;
    }

    spdlog::info("Ingested {} events for project {} ({} failed)", accepted, project_id, failed);
    return crow::response(202, body);
}

} // namespace apdl
