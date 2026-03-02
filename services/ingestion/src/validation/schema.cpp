#include "apdl/validation/schema.h"
#include <cstdint>

namespace apdl {

static constexpr int MAX_BATCH_SIZE = 500;
static constexpr int MAX_EVENT_NAME_LENGTH = 256;
static constexpr int MAX_PROPERTY_KEY_LENGTH = 256;
static constexpr int MAX_STRING_PROPERTY_LENGTH = 8192;

static const char* VALID_EVENT_TYPES[] = {
    "track", "identify", "group", "page", "screen", "alias"
};
static constexpr int NUM_VALID_TYPES = 6;

static bool is_valid_event_type(const std::string& type) {
    for (int i = 0; i < NUM_VALID_TYPES; ++i) {
        if (type == VALID_EVENT_TYPES[i]) return true;
    }
    return false;
}

ValidationResult validate_event_batch(const rapidjson::Document& doc) {
    ValidationResult result{true, {}};

    if (!doc.IsObject()) {
        result.valid = false;
        result.errors.push_back({"body", "Request body must be a JSON object"});
        return result;
    }

    if (!doc.HasMember("events")) {
        result.valid = false;
        result.errors.push_back({"events", "Missing required field 'events'"});
        return result;
    }

    if (!doc["events"].IsArray()) {
        result.valid = false;
        result.errors.push_back({"events", "Field 'events' must be an array"});
        return result;
    }

    const auto& events = doc["events"].GetArray();

    if (events.Size() == 0) {
        result.valid = false;
        result.errors.push_back({"events", "Events array must not be empty"});
        return result;
    }

    if (events.Size() > MAX_BATCH_SIZE) {
        result.valid = false;
        result.errors.push_back({
            "events",
            "Batch size " + std::to_string(events.Size()) +
            " exceeds maximum of " + std::to_string(MAX_BATCH_SIZE)
        });
        return result;
    }

    for (rapidjson::SizeType i = 0; i < events.Size(); ++i) {
        auto event_result = validate_single_event(events[i]);
        if (!event_result.valid) {
            result.valid = false;
            // Prefix each error with the event index
            std::string prefix = "events[" + std::to_string(i) + "].";
            for (auto& err : event_result.errors) {
                err.field = prefix + err.field;
                result.errors.push_back(std::move(err));
            }
        }
    }

    return result;
}

ValidationResult validate_single_event(const rapidjson::Value& event) {
    ValidationResult result{true, {}};

    if (!event.IsObject()) {
        result.valid = false;
        result.errors.push_back({"", "Event must be a JSON object"});
        return result;
    }

    // Validate "event" field (event name) -- required for track events
    bool has_event_name = event.HasMember("event") && event["event"].IsString();
    bool has_type = event.HasMember("type") && event["type"].IsString();

    // Must have either "event" (name) or "type"
    if (!has_event_name && !has_type) {
        result.valid = false;
        result.errors.push_back({"event", "Event must have either 'event' (name) or 'type' field"});
    }

    // Validate event name length
    if (has_event_name) {
        std::string name = event["event"].GetString();
        if (name.empty()) {
            result.valid = false;
            result.errors.push_back({"event", "Event name must not be empty"});
        } else if (static_cast<int>(name.size()) > MAX_EVENT_NAME_LENGTH) {
            result.valid = false;
            result.errors.push_back({
                "event",
                "Event name exceeds maximum length of " + std::to_string(MAX_EVENT_NAME_LENGTH)
            });
        }
    }

    // Validate type if present
    if (has_type) {
        std::string type = event["type"].GetString();
        if (!is_valid_event_type(type)) {
            result.valid = false;
            result.errors.push_back({
                "type",
                "Invalid event type '" + type + "'. Must be one of: track, identify, group, page, screen, alias"
            });
        }
    }

    // Must have user_id or anonymous_id
    bool has_user_id = event.HasMember("user_id") && event["user_id"].IsString() &&
                       std::string(event["user_id"].GetString()).size() > 0;
    bool has_anon_id = event.HasMember("anonymous_id") && event["anonymous_id"].IsString() &&
                       std::string(event["anonymous_id"].GetString()).size() > 0;
    // Also accept camelCase variants from the SDK
    bool has_userId = event.HasMember("userId") && event["userId"].IsString() &&
                      std::string(event["userId"].GetString()).size() > 0;
    bool has_anonymousId = event.HasMember("anonymousId") && event["anonymousId"].IsString() &&
                           std::string(event["anonymousId"].GetString()).size() > 0;

    if (!has_user_id && !has_anon_id && !has_userId && !has_anonymousId) {
        result.valid = false;
        result.errors.push_back({
            "user_id",
            "Event must have either 'user_id'/'userId' or 'anonymous_id'/'anonymousId'"
        });
    }

    // Validate timestamp if present
    if (event.HasMember("timestamp")) {
        if (!event["timestamp"].IsString()) {
            result.valid = false;
            result.errors.push_back({"timestamp", "Timestamp must be a string in ISO 8601 format"});
        }
    }

    // Validate properties if present
    if (event.HasMember("properties")) {
        if (!event["properties"].IsObject()) {
            result.valid = false;
            result.errors.push_back({"properties", "Properties must be a JSON object"});
        } else {
            const auto& props = event["properties"].GetObject();
            for (auto it = props.MemberBegin(); it != props.MemberEnd(); ++it) {
                std::string key = it->name.GetString();
                if (static_cast<int>(key.size()) > MAX_PROPERTY_KEY_LENGTH) {
                    result.valid = false;
                    result.errors.push_back({
                        "properties." + key,
                        "Property key exceeds maximum length of " + std::to_string(MAX_PROPERTY_KEY_LENGTH)
                    });
                }
                if (it->value.IsString()) {
                    std::string val = it->value.GetString();
                    if (static_cast<int>(val.size()) > MAX_STRING_PROPERTY_LENGTH) {
                        result.valid = false;
                        result.errors.push_back({
                            "properties." + key,
                            "String property value exceeds maximum length of " +
                            std::to_string(MAX_STRING_PROPERTY_LENGTH)
                        });
                    }
                }
            }
        }
    }

    // Validate traits if present
    if (event.HasMember("traits")) {
        if (!event["traits"].IsObject()) {
            result.valid = false;
            result.errors.push_back({"traits", "Traits must be a JSON object"});
        }
    }

    // Validate context if present
    if (event.HasMember("context")) {
        if (!event["context"].IsObject()) {
            result.valid = false;
            result.errors.push_back({"context", "Context must be a JSON object"});
        }
    }

    return result;
}

} // namespace apdl
