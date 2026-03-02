#include "apdl/handlers/admin.h"
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

static std::string serialize_value(const rapidjson::Value& val) {
    rapidjson::StringBuffer sb;
    rapidjson::Writer<rapidjson::StringBuffer> w(sb);
    val.Accept(w);
    return std::string(sb.GetString(), sb.GetSize());
}

static crow::response unauthorized_response() {
    crow::json::wvalue err;
    err["error"] = "unauthorized";
    err["message"] = "API key or project_id required";
    return crow::response(401, err);
}

static crow::response bad_json_response() {
    crow::json::wvalue err;
    err["error"] = "bad_request";
    err["message"] = "Invalid JSON body";
    return crow::response(400, err);
}

// ---- Flag Admin CRUD ----

crow::response admin_create_flag(const crow::request& req,
                                  PostgresStore& pg,
                                  RedisCache& cache,
                                  SSEBroadcaster& broadcaster) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) return unauthorized_response();

    rapidjson::Document doc;
    doc.Parse(req.body.c_str(), req.body.size());
    if (doc.HasParseError() || !doc.IsObject()) return bad_json_response();

    if (!doc.HasMember("key") || !doc["key"].IsString()) {
        crow::json::wvalue err;
        err["error"] = "bad_request";
        err["message"] = "Field 'key' is required";
        return crow::response(400, err);
    }

    // Check for duplicate
    auto existing = pg.getFlag(project_id, doc["key"].GetString());
    if (existing.has_value()) {
        crow::json::wvalue err;
        err["error"] = "conflict";
        err["message"] = "Flag with key '" + std::string(doc["key"].GetString()) + "' already exists";
        return crow::response(409, err);
    }

    FlagConfig flag;
    flag.project_id = project_id;
    flag.key = doc["key"].GetString();
    flag.enabled = doc.HasMember("enabled") && doc["enabled"].IsBool() ? doc["enabled"].GetBool() : false;
    flag.description = doc.HasMember("description") && doc["description"].IsString() ? doc["description"].GetString() : "";
    flag.variant_type = doc.HasMember("variant_type") && doc["variant_type"].IsString() ? doc["variant_type"].GetString() : "boolean";
    flag.default_value = doc.HasMember("default_value") && doc["default_value"].IsString() ? doc["default_value"].GetString() : "false";
    flag.rollout_percentage = doc.HasMember("rollout_percentage") && doc["rollout_percentage"].IsNumber() ? doc["rollout_percentage"].GetDouble() : 100.0;

    if (doc.HasMember("rules") && doc["rules"].IsArray()) {
        flag.rules_json = serialize_value(doc["rules"]);
    } else {
        flag.rules_json = "[]";
    }
    if (doc.HasMember("variants") && doc["variants"].IsArray()) {
        flag.variants_json = serialize_value(doc["variants"]);
    } else {
        flag.variants_json = "[]";
    }

    if (!pg.createFlag(flag)) {
        crow::json::wvalue err;
        err["error"] = "internal_error";
        err["message"] = "Failed to create flag in database";
        return crow::response(500, err);
    }

    // Invalidate cache and broadcast update
    cache.invalidateFlags(project_id);

    rapidjson::StringBuffer broadcast_buf;
    rapidjson::Writer<rapidjson::StringBuffer> bw(broadcast_buf);
    bw.StartObject();
    bw.Key("action");
    bw.String("flag_created");
    bw.Key("key");
    bw.String(flag.key.c_str());
    bw.Key("enabled");
    bw.Bool(flag.enabled);
    bw.EndObject();
    broadcaster.broadcast(project_id, "flag_update",
                          std::string(broadcast_buf.GetString(), broadcast_buf.GetSize()));

    spdlog::info("Flag '{}' created for project {}", flag.key, project_id);

    crow::json::wvalue body;
    body["created"] = true;
    body["key"] = flag.key;
    return crow::response(201, body);
}

crow::response admin_update_flag(const crow::request& req,
                                  const std::string& flag_key,
                                  PostgresStore& pg,
                                  RedisCache& cache,
                                  SSEBroadcaster& broadcaster) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) return unauthorized_response();

    auto existing = pg.getFlag(project_id, flag_key);
    if (!existing.has_value()) {
        crow::json::wvalue err;
        err["error"] = "not_found";
        err["message"] = "Flag '" + flag_key + "' not found";
        return crow::response(404, err);
    }

    rapidjson::Document doc;
    doc.Parse(req.body.c_str(), req.body.size());
    if (doc.HasParseError() || !doc.IsObject()) return bad_json_response();

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
    if (doc.HasMember("rules") && doc["rules"].IsArray())
        flag.rules_json = serialize_value(doc["rules"]);
    if (doc.HasMember("variants") && doc["variants"].IsArray())
        flag.variants_json = serialize_value(doc["variants"]);

    if (!pg.updateFlag(flag)) {
        crow::json::wvalue err;
        err["error"] = "internal_error";
        err["message"] = "Failed to update flag";
        return crow::response(500, err);
    }

    cache.invalidateFlags(project_id);

    rapidjson::StringBuffer broadcast_buf;
    rapidjson::Writer<rapidjson::StringBuffer> bw(broadcast_buf);
    bw.StartObject();
    bw.Key("action");
    bw.String("flag_updated");
    bw.Key("key");
    bw.String(flag.key.c_str());
    bw.Key("enabled");
    bw.Bool(flag.enabled);
    bw.EndObject();
    broadcaster.broadcast(project_id, "flag_update",
                          std::string(broadcast_buf.GetString(), broadcast_buf.GetSize()));

    spdlog::info("Flag '{}' updated for project {}", flag.key, project_id);

    crow::json::wvalue body;
    body["updated"] = true;
    body["key"] = flag.key;
    return crow::response(200, body);
}

crow::response admin_delete_flag(const crow::request& req,
                                  const std::string& flag_key,
                                  PostgresStore& pg,
                                  RedisCache& cache,
                                  SSEBroadcaster& broadcaster) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) return unauthorized_response();

    if (!pg.deleteFlag(project_id, flag_key)) {
        crow::json::wvalue err;
        err["error"] = "not_found";
        err["message"] = "Flag '" + flag_key + "' not found or already deleted";
        return crow::response(404, err);
    }

    cache.invalidateFlags(project_id);

    rapidjson::StringBuffer broadcast_buf;
    rapidjson::Writer<rapidjson::StringBuffer> bw(broadcast_buf);
    bw.StartObject();
    bw.Key("action");
    bw.String("flag_deleted");
    bw.Key("key");
    bw.String(flag_key.c_str());
    bw.EndObject();
    broadcaster.broadcast(project_id, "flag_update",
                          std::string(broadcast_buf.GetString(), broadcast_buf.GetSize()));

    spdlog::info("Flag '{}' deleted for project {}", flag_key, project_id);

    crow::json::wvalue body;
    body["deleted"] = true;
    body["key"] = flag_key;
    return crow::response(200, body);
}

crow::response admin_list_flags(const crow::request& req, PostgresStore& pg) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) return unauthorized_response();

    auto flags = pg.getFlags(project_id);

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
        writer.RawValue(f.default_value.c_str(), f.default_value.size(), rapidjson::kStringType);
        writer.Key("rollout_percentage");
        writer.Double(f.rollout_percentage);
        writer.Key("created_at");
        writer.String(f.created_at.c_str());
        writer.Key("updated_at");
        writer.String(f.updated_at.c_str());
        writer.EndObject();
    }

    writer.EndArray();
    writer.Key("count");
    writer.Int(static_cast<int>(flags.size()));
    writer.EndObject();

    crow::response res(200, std::string(buf.GetString(), buf.GetSize()));
    res.set_header("Content-Type", "application/json");
    return res;
}

// ---- Experiment Admin CRUD ----

crow::response admin_create_experiment(const crow::request& req,
                                        PostgresStore& pg,
                                        RedisCache& cache,
                                        SSEBroadcaster& broadcaster) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) return unauthorized_response();

    rapidjson::Document doc;
    doc.Parse(req.body.c_str(), req.body.size());
    if (doc.HasParseError() || !doc.IsObject()) return bad_json_response();

    if (!doc.HasMember("key") || !doc["key"].IsString()) {
        crow::json::wvalue err;
        err["error"] = "bad_request";
        err["message"] = "Field 'key' is required";
        return crow::response(400, err);
    }

    auto existing = pg.getExperiment(project_id, doc["key"].GetString());
    if (existing.has_value()) {
        crow::json::wvalue err;
        err["error"] = "conflict";
        err["message"] = "Experiment with key '" + std::string(doc["key"].GetString()) + "' already exists";
        return crow::response(409, err);
    }

    ExperimentConfig exp;
    exp.project_id = project_id;
    exp.key = doc["key"].GetString();
    exp.status = doc.HasMember("status") && doc["status"].IsString() ? doc["status"].GetString() : "draft";
    exp.description = doc.HasMember("description") && doc["description"].IsString() ? doc["description"].GetString() : "";
    exp.traffic_percentage = doc.HasMember("traffic_percentage") && doc["traffic_percentage"].IsNumber() ? doc["traffic_percentage"].GetDouble() : 100.0;
    exp.start_date = doc.HasMember("start_date") && doc["start_date"].IsString() ? doc["start_date"].GetString() : "";
    exp.end_date = doc.HasMember("end_date") && doc["end_date"].IsString() ? doc["end_date"].GetString() : "";

    if (doc.HasMember("variants") && doc["variants"].IsArray()) {
        exp.variants_json = serialize_value(doc["variants"]);
    } else {
        exp.variants_json = "[]";
    }
    if (doc.HasMember("targeting_rules") && doc["targeting_rules"].IsArray()) {
        exp.targeting_rules_json = serialize_value(doc["targeting_rules"]);
    } else {
        exp.targeting_rules_json = "[]";
    }

    if (!pg.createExperiment(exp)) {
        crow::json::wvalue err;
        err["error"] = "internal_error";
        err["message"] = "Failed to create experiment";
        return crow::response(500, err);
    }

    cache.invalidateExperiments(project_id);

    rapidjson::StringBuffer broadcast_buf;
    rapidjson::Writer<rapidjson::StringBuffer> bw(broadcast_buf);
    bw.StartObject();
    bw.Key("action");
    bw.String("experiment_created");
    bw.Key("key");
    bw.String(exp.key.c_str());
    bw.Key("status");
    bw.String(exp.status.c_str());
    bw.EndObject();
    broadcaster.broadcast(project_id, "experiment_update",
                          std::string(broadcast_buf.GetString(), broadcast_buf.GetSize()));

    spdlog::info("Experiment '{}' created for project {}", exp.key, project_id);

    crow::json::wvalue body;
    body["created"] = true;
    body["key"] = exp.key;
    return crow::response(201, body);
}

crow::response admin_update_experiment(const crow::request& req,
                                        const std::string& experiment_key,
                                        PostgresStore& pg,
                                        RedisCache& cache,
                                        SSEBroadcaster& broadcaster) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) return unauthorized_response();

    auto existing = pg.getExperiment(project_id, experiment_key);
    if (!existing.has_value()) {
        crow::json::wvalue err;
        err["error"] = "not_found";
        err["message"] = "Experiment '" + experiment_key + "' not found";
        return crow::response(404, err);
    }

    rapidjson::Document doc;
    doc.Parse(req.body.c_str(), req.body.size());
    if (doc.HasParseError() || !doc.IsObject()) return bad_json_response();

    ExperimentConfig exp = existing.value();

    if (doc.HasMember("status") && doc["status"].IsString())
        exp.status = doc["status"].GetString();
    if (doc.HasMember("description") && doc["description"].IsString())
        exp.description = doc["description"].GetString();
    if (doc.HasMember("traffic_percentage") && doc["traffic_percentage"].IsNumber())
        exp.traffic_percentage = doc["traffic_percentage"].GetDouble();
    if (doc.HasMember("start_date") && doc["start_date"].IsString())
        exp.start_date = doc["start_date"].GetString();
    if (doc.HasMember("end_date") && doc["end_date"].IsString())
        exp.end_date = doc["end_date"].GetString();
    if (doc.HasMember("variants") && doc["variants"].IsArray())
        exp.variants_json = serialize_value(doc["variants"]);
    if (doc.HasMember("targeting_rules") && doc["targeting_rules"].IsArray())
        exp.targeting_rules_json = serialize_value(doc["targeting_rules"]);

    if (!pg.updateExperiment(exp)) {
        crow::json::wvalue err;
        err["error"] = "internal_error";
        err["message"] = "Failed to update experiment";
        return crow::response(500, err);
    }

    cache.invalidateExperiments(project_id);

    rapidjson::StringBuffer broadcast_buf;
    rapidjson::Writer<rapidjson::StringBuffer> bw(broadcast_buf);
    bw.StartObject();
    bw.Key("action");
    bw.String("experiment_updated");
    bw.Key("key");
    bw.String(exp.key.c_str());
    bw.Key("status");
    bw.String(exp.status.c_str());
    bw.EndObject();
    broadcaster.broadcast(project_id, "experiment_update",
                          std::string(broadcast_buf.GetString(), broadcast_buf.GetSize()));

    spdlog::info("Experiment '{}' updated for project {}", exp.key, project_id);

    crow::json::wvalue body;
    body["updated"] = true;
    body["key"] = exp.key;
    return crow::response(200, body);
}

crow::response admin_delete_experiment(const crow::request& req,
                                        const std::string& experiment_key,
                                        PostgresStore& pg,
                                        RedisCache& cache,
                                        SSEBroadcaster& broadcaster) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) return unauthorized_response();

    if (!pg.deleteExperiment(project_id, experiment_key)) {
        crow::json::wvalue err;
        err["error"] = "not_found";
        err["message"] = "Experiment '" + experiment_key + "' not found or already deleted";
        return crow::response(404, err);
    }

    cache.invalidateExperiments(project_id);

    rapidjson::StringBuffer broadcast_buf;
    rapidjson::Writer<rapidjson::StringBuffer> bw(broadcast_buf);
    bw.StartObject();
    bw.Key("action");
    bw.String("experiment_deleted");
    bw.Key("key");
    bw.String(experiment_key.c_str());
    bw.EndObject();
    broadcaster.broadcast(project_id, "experiment_update",
                          std::string(broadcast_buf.GetString(), broadcast_buf.GetSize()));

    spdlog::info("Experiment '{}' deleted for project {}", experiment_key, project_id);

    crow::json::wvalue body;
    body["deleted"] = true;
    body["key"] = experiment_key;
    return crow::response(200, body);
}

crow::response admin_list_experiments(const crow::request& req, PostgresStore& pg) {
    std::string project_id = extract_project_id(req);
    if (project_id.empty()) return unauthorized_response();

    auto experiments = pg.getExperiments(project_id);

    rapidjson::StringBuffer buf;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buf);

    writer.StartObject();
    writer.Key("experiments");
    writer.StartArray();

    for (const auto& e : experiments) {
        writer.StartObject();
        writer.Key("key");
        writer.String(e.key.c_str());
        writer.Key("status");
        writer.String(e.status.c_str());
        writer.Key("description");
        writer.String(e.description.c_str());
        writer.Key("traffic_percentage");
        writer.Double(e.traffic_percentage);

        if (!e.variants_json.empty() && e.variants_json != "[]") {
            writer.Key("variants");
            writer.RawValue(e.variants_json.c_str(), e.variants_json.size(), rapidjson::kArrayType);
        }

        writer.Key("start_date");
        writer.String(e.start_date.c_str());
        writer.Key("end_date");
        writer.String(e.end_date.c_str());
        writer.Key("created_at");
        writer.String(e.created_at.c_str());
        writer.Key("updated_at");
        writer.String(e.updated_at.c_str());
        writer.EndObject();
    }

    writer.EndArray();
    writer.Key("count");
    writer.Int(static_cast<int>(experiments.size()));
    writer.EndObject();

    crow::response res(200, std::string(buf.GetString(), buf.GetSize()));
    res.set_header("Content-Type", "application/json");
    return res;
}

} // namespace apdl
