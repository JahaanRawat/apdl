#include <gtest/gtest.h>
#include "apdl/store/postgres.h"
#include "apdl/flags/evaluator.h"

namespace apdl {
namespace test {

class FlagConfigTest : public ::testing::Test {
protected:
    FlagConfig make_flag(const std::string& key, bool enabled, double rollout = 100.0) {
        FlagConfig flag;
        flag.key = key;
        flag.project_id = "test_project";
        flag.enabled = enabled;
        flag.variant_type = "boolean";
        flag.default_value = "false";
        flag.rollout_percentage = rollout;
        flag.rules_json = "[]";
        flag.variants_json = "[]";
        flag.created_at = "2025-01-01T00:00:00Z";
        flag.updated_at = "2025-01-01T00:00:00Z";
        return flag;
    }
};

TEST_F(FlagConfigTest, DefaultFlagValues) {
    FlagConfig flag;
    EXPECT_FALSE(flag.enabled);
    EXPECT_EQ(flag.rollout_percentage, 100.0);
    EXPECT_TRUE(flag.key.empty());
}

TEST_F(FlagConfigTest, FlagWithAllFields) {
    auto flag = make_flag("test_feature", true, 50.0);
    flag.description = "Test feature flag";
    flag.variant_type = "string";
    flag.default_value = "\"control\"";
    flag.rules_json = R"([{"conditions":[{"attribute":"plan","operator":"equals","value":"pro"}]}])";
    flag.variants_json = R"([{"key":"control","value":"control","weight":50},{"key":"variant_a","value":"variant_a","weight":50}])";

    EXPECT_EQ(flag.key, "test_feature");
    EXPECT_TRUE(flag.enabled);
    EXPECT_EQ(flag.rollout_percentage, 50.0);
    EXPECT_EQ(flag.variant_type, "string");
    EXPECT_FALSE(flag.rules_json.empty());
    EXPECT_FALSE(flag.variants_json.empty());
}

TEST_F(FlagConfigTest, ExperimentDefaultValues) {
    ExperimentConfig exp;
    EXPECT_TRUE(exp.key.empty());
    EXPECT_TRUE(exp.status.empty());
    EXPECT_EQ(exp.traffic_percentage, 100.0);
}

TEST_F(FlagConfigTest, ExperimentWithAllFields) {
    ExperimentConfig exp;
    exp.key = "pricing_test";
    exp.project_id = "proj_1";
    exp.status = "running";
    exp.description = "A/B test for pricing page";
    exp.traffic_percentage = 80.0;
    exp.variants_json = R"([{"key":"control","weight":50},{"key":"new_pricing","weight":50}])";
    exp.targeting_rules_json = R"([{"attribute":"country","operator":"in","value":["US","CA"]}])";

    EXPECT_EQ(exp.key, "pricing_test");
    EXPECT_EQ(exp.status, "running");
    EXPECT_EQ(exp.traffic_percentage, 80.0);
}

} // namespace test
} // namespace apdl
