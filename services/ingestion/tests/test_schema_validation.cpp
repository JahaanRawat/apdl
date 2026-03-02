#include <gtest/gtest.h>
#include "apdl/validation/schema.h"
#include "rapidjson/document.h"

#include <string>

namespace apdl {
namespace test {

class SchemaValidationTest : public ::testing::Test {
protected:
    rapidjson::Document parse(const std::string& json) {
        rapidjson::Document doc;
        doc.Parse(json.c_str(), json.size());
        return doc;
    }

    // Parse a JSON string and return just the first element as a Value reference
    rapidjson::Document parse_event(const std::string& json) {
        rapidjson::Document doc;
        doc.Parse(json.c_str(), json.size());
        return doc;
    }
};

// ---- Batch validation tests ----

TEST_F(SchemaValidationTest, ValidMinimalBatch) {
    auto doc = parse(R"({"events": [{"event": "click", "user_id": "u1"}]})");
    auto result = validate_event_batch(doc);
    EXPECT_TRUE(result.valid);
    EXPECT_EQ(result.errors.size(), 0u);
}

TEST_F(SchemaValidationTest, BatchMissingEventsKey) {
    auto doc = parse(R"({"items": []})");
    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    ASSERT_EQ(result.errors.size(), 1u);
    EXPECT_EQ(result.errors[0].field, "events");
    EXPECT_NE(result.errors[0].message.find("Missing"), std::string::npos);
}

TEST_F(SchemaValidationTest, BatchEventsNotArray) {
    auto doc = parse(R"({"events": "not_array"})");
    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    ASSERT_EQ(result.errors.size(), 1u);
    EXPECT_EQ(result.errors[0].field, "events");
}

TEST_F(SchemaValidationTest, BatchEmpty) {
    auto doc = parse(R"({"events": []})");
    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(SchemaValidationTest, BatchExceedsMaxSize) {
    // Build a JSON with 501 events
    std::string json = R"({"events": [)";
    for (int i = 0; i < 501; ++i) {
        if (i > 0) json += ",";
        json += R"({"event":"e","user_id":"u"})";
    }
    json += "]}";

    auto doc = parse(json);
    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    bool found_size_error = false;
    for (const auto& err : result.errors) {
        if (err.message.find("exceeds maximum") != std::string::npos) {
            found_size_error = true;
            break;
        }
    }
    EXPECT_TRUE(found_size_error);
}

TEST_F(SchemaValidationTest, BatchAtMaxSize) {
    // Build a JSON with exactly 500 events - should be valid
    std::string json = R"({"events": [)";
    for (int i = 0; i < 500; ++i) {
        if (i > 0) json += ",";
        json += R"({"event":"e","user_id":"u"})";
    }
    json += "]}";

    auto doc = parse(json);
    auto result = validate_event_batch(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(SchemaValidationTest, NonObjectBody) {
    auto doc = parse(R"("just a string")");
    auto result = validate_event_batch(doc);
    EXPECT_FALSE(result.valid);
    ASSERT_GE(result.errors.size(), 1u);
    EXPECT_EQ(result.errors[0].field, "body");
}

// ---- Single event validation tests ----

TEST_F(SchemaValidationTest, ValidTrackEvent) {
    auto doc = parse_event(R"({
        "event": "purchase",
        "type": "track",
        "user_id": "usr_42",
        "properties": {"amount": 99.99, "currency": "USD"},
        "timestamp": "2025-06-15T10:30:00.000Z"
    })");
    auto result = validate_single_event(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(SchemaValidationTest, ValidIdentifyEvent) {
    auto doc = parse_event(R"({
        "type": "identify",
        "user_id": "usr_42",
        "traits": {"name": "Alice", "plan": "pro"}
    })");
    auto result = validate_single_event(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(SchemaValidationTest, ValidPageEvent) {
    auto doc = parse_event(R"({
        "type": "page",
        "anonymous_id": "anon_xyz",
        "properties": {"url": "/pricing", "title": "Pricing"}
    })");
    auto result = validate_single_event(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(SchemaValidationTest, ValidGroupEvent) {
    auto doc = parse_event(R"({
        "type": "group",
        "user_id": "usr_1",
        "properties": {"company": "Acme Inc"}
    })");
    auto result = validate_single_event(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(SchemaValidationTest, EventWithOnlyAnonymousId) {
    auto doc = parse_event(R"({
        "event": "page_view",
        "anonymous_id": "anon_abc"
    })");
    auto result = validate_single_event(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(SchemaValidationTest, EventWithOnlyCamelCaseAnonymousId) {
    auto doc = parse_event(R"({
        "event": "page_view",
        "anonymousId": "anon_abc"
    })");
    auto result = validate_single_event(doc);
    EXPECT_TRUE(result.valid);
}

TEST_F(SchemaValidationTest, EventMissingNameAndType) {
    auto doc = parse_event(R"({
        "user_id": "usr_1",
        "properties": {"key": "val"}
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
    bool found = false;
    for (const auto& e : result.errors) {
        if (e.field == "event") found = true;
    }
    EXPECT_TRUE(found);
}

TEST_F(SchemaValidationTest, EventMissingIdentifier) {
    auto doc = parse_event(R"({
        "event": "test_event",
        "type": "track"
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
    bool found = false;
    for (const auto& e : result.errors) {
        if (e.field == "user_id") found = true;
    }
    EXPECT_TRUE(found);
}

TEST_F(SchemaValidationTest, EventEmptyUserIdTreatedAsMissing) {
    auto doc = parse_event(R"({
        "event": "test",
        "user_id": ""
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(SchemaValidationTest, EventEmptyName) {
    auto doc = parse_event(R"({
        "event": "",
        "user_id": "usr_1"
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(SchemaValidationTest, EventInvalidType) {
    auto doc = parse_event(R"({
        "type": "banana",
        "event": "test",
        "user_id": "usr_1"
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
    bool found = false;
    for (const auto& e : result.errors) {
        if (e.field == "type") found = true;
    }
    EXPECT_TRUE(found);
}

TEST_F(SchemaValidationTest, EventAllValidTypes) {
    const char* types[] = {"track", "identify", "group", "page", "screen", "alias"};
    for (const char* t : types) {
        std::string json = R"({"type": ")" + std::string(t) + R"(", "user_id": "u1"})";
        auto doc = parse_event(json);
        auto result = validate_single_event(doc);
        EXPECT_TRUE(result.valid) << "Type '" << t << "' should be valid";
    }
}

TEST_F(SchemaValidationTest, EventPropertiesNotObject) {
    auto doc = parse_event(R"({
        "event": "test",
        "user_id": "usr_1",
        "properties": [1, 2, 3]
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(SchemaValidationTest, EventTraitsNotObject) {
    auto doc = parse_event(R"({
        "type": "identify",
        "user_id": "usr_1",
        "traits": "not_object"
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(SchemaValidationTest, EventContextNotObject) {
    auto doc = parse_event(R"({
        "event": "test",
        "user_id": "usr_1",
        "context": 42
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(SchemaValidationTest, EventTimestampNotString) {
    auto doc = parse_event(R"({
        "event": "test",
        "user_id": "usr_1",
        "timestamp": 1234567890
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(SchemaValidationTest, EventNotAnObject) {
    auto doc = parse_event(R"("just a string")");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
}

TEST_F(SchemaValidationTest, MultipleErrorsCollected) {
    auto doc = parse_event(R"({
        "properties": "invalid",
        "traits": 42,
        "context": [1]
    })");
    auto result = validate_single_event(doc);
    EXPECT_FALSE(result.valid);
    // Should have errors for: missing event/type, missing user_id, invalid properties, invalid traits, invalid context
    EXPECT_GE(result.errors.size(), 4u);
}

} // namespace test
} // namespace apdl
