#include "apdl/handlers/flags.h"
#include "apdl/flags/evaluator.h"
#include "spdlog/spdlog.h"
#include "rapidjson/document.h"
#include "rapidjson/writer.h"
#include "rapidjson/stringbuffer.h"

namespace apdl {

static std::string extract_project_id(const crow::request& req) {
    // Extract from X-API-Key header
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

    // Also check query param for project_id directly
    auto url_params = crow::query_string(req.url_params);
    const char* pid = url_params.get("project_id");
    if (pid) return std::string(pid);

    return "";
}

static std::string flags_to_json(const std::vector<FlagConfig>& flags) {
    rapidjson::StringBuffer buf;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buf);

    writer.StartObject();
    writer.Key("flags");
    writer.StartArray();

    for (const auto& f : flags) {
        writer.StartObject();
        writer.Key("key");
        writer.String(f.key.c_str());
        writer.Key("enabled");
        writer.Bool(f.enabled);
        writer.Key("description");
        writer.String(f.description.c_str());
        writer.Key("variant_type");
        writer.String(f.variant_type.c_str());
        writer.Key("default_value");
        // default_value is JSON-encoded, write it raw
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
    writer.EndObject();

    return std::string(buf.GetString(), buf.GetSize());
}

crow::response handle_get_flags(const crow::request& req, PostgresStore& pg, RedisCache& cache) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) {
        crow::json::wvalue err;
        err["error"] = "unauthorized";
        err["message"] = "API key or project_id required";
        return crow::response(401, err);
    }

    // Check Redis cache first
    auto cached = cache.getFlags(project_id);
    if (cached.has_value()) {
        spdlog::debug("Cache hit for flags of project {}", project_id);
        crow::response res(200, cached.value());
        res.set_header("Content-Type", "application/json");
        res.set_header("X-Cache", "HIT");
        return res;
    }

    // Cache miss -- query PostgreSQL
    spdlog::debug("Cache miss for flags of project {}, querying Postgres", project_id);
    auto flags = pg.getFlags(project_id);

    std::string json = flags_to_json(flags);

    // Populate cache
    cache.setFlags(project_id, json, 60);

    crow::response res(200, json);
    res.set_header("Content-Type", "application/json");
    res.set_header("X-Cache", "MISS");
    return res;
}

crow::response handle_create_flag(const crow::request& req, PostgresStore& pg, RedisCache& cache) {
    // Delegate to admin handler -- this is here for backward compat
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) {
        crow::json::wvalue err;
        err["error"] = "unauthorized";
        err["message"] = "API key or project_id required";
        return crow::response(401, err);
    }

    rapidjson::Document doc;
    doc.Parse(req.body.c_str(), req.body.size());
    if (doc.HasParseError() || !doc.IsObject()) {
        crow::json::wvalue err;
        err["error"] = "bad_request";
        err["message"] = "Invalid JSON body";
        return crow::response(400, err);
    }

    FlagConfig flag;
    flag.project_id = project_id;

    if (!doc.HasMember("key") || !doc["key"].IsString()) {
        crow::json::wvalue err;
        err["error"] = "bad_request";
        err["message"] = "Field 'key' is required and must be a string";
        return crow::response(400, err);
    }
    flag.key = doc["key"].GetString();

    flag.enabled = doc.HasMember("enabled") && doc["enabled"].IsBool() ? doc["enabled"].GetBool() : false;
    flag.description = doc.HasMember("description") && doc["description"].IsString() ? doc["description"].GetString() : "";
    flag.variant_type = doc.HasMember("variant_type") && doc["variant_type"].IsString() ? doc["variant_type"].GetString() : "boolean";
    flag.default_value = doc.HasMember("default_value") && doc["default_value"].IsString() ? doc["default_value"].GetString() : "false";
    flag.rollout_percentage = doc.HasMember("rollout_percentage") && doc["rollout_percentage"].IsNumber() ? doc["rollout_percentage"].GetDouble() : 100.0;
    flag.rules_json = doc.HasMember("rules") ? "" : "[]";
    flag.variants_json = doc.HasMember("variants") ? "" : "[]";

    // Serialize rules/variants if present
    if (doc.HasMember("rules") && doc["rules"].IsArray()) {
        rapidjson::StringBuffer sb;
        rapidjson::Writer<rapidjson::StringBuffer> w(sb);
        doc["rules"].Accept(w);
        flag.rules_json = std::string(sb.GetString(), sb.GetSize());
    }
    if (doc.HasMember("variants") && doc["variants"].IsArray()) {
        rapidjson::StringBuffer sb;
        rapidjson::Writer<rapidjson::StringBuffer> w(sb);
        doc["variants"].Accept(w);
        flag.variants_json = std::string(sb.GetString(), sb.GetSize());
    }

    if (!pg.createFlag(flag)) {
        crow::json::wvalue err;
        err["error"] = "internal_error";
        err["message"] = "Failed to create flag";
        return crow::response(500, err);
    }

    cache.invalidateFlags(project_id);

    crow::json::wvalue body;
    body["created"] = true;
    body["key"] = flag.key;
    return crow::response(201, body);
}

crow::response handle_update_flag(const crow::request& req, const std::string& flag_key,
                                   PostgresStore& pg, RedisCache& cache) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) {
        crow::json::wvalue err;
        err["error"] = "unauthorized";
        return crow::response(401, err);
    }

    auto existing = pg.getFlag(project_id, flag_key);
    if (!existing.has_value()) {
        crow::json::wvalue err;
        err["error"] = "not_found";
        err["message"] = "Flag '" + flag_key + "' not found";
        return crow::response(404, err);
    }

    rapidjson::Document doc;
    doc.Parse(req.body.c_str(), req.body.size());
    if (doc.HasParseError() || !doc.IsObject()) {
        crow::json::wvalue err;
        err["error"] = "bad_request";
        err["message"] = "Invalid JSON body";
        return crow::response(400, err);
    }

    FlagConfig flag = existing.value();

    if (doc.HasMember("enabled") && doc["enabled"].IsBool())
        flag.enabled = doc["enabled"].GetBool();
    if (doc.HasMember("description") && doc["description"].IsString())
        flag.description = doc["description"].GetString();
    if (doc.HasMember("variant_type") && doc["variant_type"].IsString())
        flag.variant_type = doc["variant_type"].GetString();
    if (doc.HasMember("default_value") && doc["default_value"].IsString())
        flag.default_value = doc["default_value"].GetString();
    if (doc.HasMember("rollout_percentage") && doc["rollout_percentage"].IsNumber())
        flag.rollout_percentage = doc["rollout_percentage"].GetDouble();
    if (doc.HasMember("rules") && doc["rules"].IsArray()) {
        rapidjson::StringBuffer sb;
        rapidjson::Writer<rapidjson::StringBuffer> w(sb);
        doc["rules"].Accept(w);
        flag.rules_json = std::string(sb.GetString(), sb.GetSize());
    }
    if (doc.HasMember("variants") && doc["variants"].IsArray()) {
        rapidjson::StringBuffer sb;
        rapidjson::Writer<rapidjson::StringBuffer> w(sb);
        doc["variants"].Accept(w);
        flag.variants_json = std::string(sb.GetString(), sb.GetSize());
    }

    if (!pg.updateFlag(flag)) {
        crow::json::wvalue err;
        err["error"] = "internal_error";
        err["message"] = "Failed to update flag";
        return crow::response(500, err);
    }

    cache.invalidateFlags(project_id);

    crow::json::wvalue body;
    body["updated"] = true;
    body["key"] = flag.key;
    return crow::response(200, body);
}

crow::response handle_delete_flag(const crow::request& req, const std::string& flag_key,
                                   PostgresStore& pg, RedisCache& cache) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) {
        crow::json::wvalue err;
        err["error"] = "unauthorized";
        return crow::response(401, err);
    }

    if (!pg.deleteFlag(project_id, flag_key)) {
        crow::json::wvalue err;
        err["error"] = "not_found";
        err["message"] = "Flag '" + flag_key + "' not found or already deleted";
        return crow::response(404, err);
    }

    cache.invalidateFlags(project_id);

    crow::json::wvalue body;
    body["deleted"] = true;
    body["key"] = flag_key;
    return crow::response(200, body);
}

} // namespace apdl
