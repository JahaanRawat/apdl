#include <gtest/gtest.h>
#include "apdl/handlers/events.h"
#include "apdl/streaming/redis_producer.h"
#include "apdl/validation/schema.h"
#include "apdl/util/json.h"
#include "rapidjson/document.h"

namespace apdl {
namespace test {

// Tests for event handler logic that don't require a live Redis connection.
// These test the validation and parsing that happens before Redis publish.

class EventHandlerTest : public ::testing::Test {
protected:
    rapidjson::Document parse(const std::string& json) {
        rapidjson::Document doc;
        doc.Parse(json.c_str(), json.size());
        return doc;
    }
};

TEST_F(EventHandlerTest, ValidBatchWithTrackEvent) {
    auto doc = parse(R"({
        "events": [{
            "event": "button_click",
            "type": "track",
            "user_id": "usr_123",
            "properties": {"button": "signup"},
            "timestamp": "2025-01-01T00:00:00.000Z"
        }]
    })");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_TRUE(result.valid);
    EXPECT_TRUE(result.errors.empty());
}

TEST_F(EventHandlerTest, ValidBatchWithAnonymousId) {
    auto doc = parse(R"({
        "events": [{
            "event": "page_view",
            "anonymous_id": "anon_abc123",
            "properties": {"url": "/home"}
        }]
    })");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(EventHandlerTest, ValidBatchWithCamelCaseIds) {
    auto doc = parse(R"({
        "events": [{
            "event": "page_view",
            "type": "page",
            "anonymousId": "anon_abc123",
            "userId": "user_456"
        }]
    })");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(EventHandlerTest, RejectMissingEventsField) {
    auto doc = parse(R"({"data": []})");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    ASSERT_GE(result.errors.size(), 1u);
    EXPECT_EQ(result.errors[0].field, "events");
}

TEST_F(EventHandlerTest, RejectEmptyEventsArray) {
    auto doc = parse(R"({"events": []})");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    ASSERT_GE(result.errors.size(), 1u);
    EXPECT_EQ(result.errors[0].field, "events");
}

TEST_F(EventHandlerTest, RejectEventWithoutIdentifier) {
    auto doc = parse(R"({
        "events": [{
            "event": "test_event",
            "properties": {}
        }]
    })");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    bool found_user_id_error = false;
    for (const auto& err : result.errors) {
        if (err.field.find("user_id") != std::string::npos) {
            found_user_id_error = true;
            break;
        }
    }
    EXPECT_TRUE(found_user_id_error);
}

TEST_F(EventHandlerTest, RejectEventWithoutNameOrType) {
    auto doc = parse(R"({
        "events": [{
            "user_id": "usr_123",
            "properties": {"key": "val"}
        }]
    })");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    bool found_event_error = false;
    for (const auto& err : result.errors) {
        if (err.field.find("event") != std::string::npos) {
            found_event_error = true;
            break;
        }
    }
    EXPECT_TRUE(found_event_error);
}

TEST_F(EventHandlerTest, RejectInvalidEventType) {
    auto doc = parse(R"({
        "events": [{
            "type": "invalid_type",
            "event": "test",
            "user_id": "usr_123"
        }]
    })");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(EventHandlerTest, RejectNonObjectBody) {
    auto doc = parse(R"([1, 2, 3])");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    ASSERT_GE(result.errors.size(), 1u);
    EXPECT_EQ(result.errors[0].field, "body");
}

TEST_F(EventHandlerTest, MultipleMixedValidAndInvalidEvents) {
    auto doc = parse(R"({
        "events": [
            {"event": "valid_event", "user_id": "usr_1"},
            {"properties": {"no_name": true}},
            {"event": "another_valid", "anonymous_id": "anon_1"}
        ]
    })");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    // Event at index 1 has no event name/type and no user_id
    bool found_idx1_error = false;
    for (const auto& err : result.errors) {
        if (err.field.find("events[1]") != std::string::npos) {
            found_idx1_error = true;
            break;
        }
    }
    EXPECT_TRUE(found_idx1_error);
}

TEST_F(EventHandlerTest, ValidIdentifyEvent) {
    auto doc = parse(R"({
        "events": [{
            "type": "identify",
            "user_id": "usr_123",
            "traits": {"name": "Jane Doe", "email": "jane@example.com"}
        }]
    })");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(EventHandlerTest, RejectInvalidProperties) {
    auto doc = parse(R"({
        "events": [{
            "event": "test",
            "user_id": "usr_1",
            "properties": "not_an_object"
        }]
    })");
    ASSERT_FALSE(doc.HasParseError());

    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(EventHandlerTest, SerializeEventJson) {
    auto doc = parse(R"({"key": "value", "num": 42})");
    ASSERT_FALSE(doc.HasParseError());

    std::string serialized = serialize(doc);
    EXPECT_FALSE(serialized.empty());
    EXPECT_NE(serialized.find("key"), std::string::npos);
    EXPECT_NE(serialized.find("value"), std::string::npos);
    EXPECT_NE(serialized.find("42"), std::string::npos);
}

} // namespace test
} // namespace apdl
