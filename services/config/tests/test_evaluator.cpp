#include <gtest/gtest.h>
#include "apdl/flags/evaluator.h"

namespace apdl {
namespace test {

class EvaluatorTest : public ::testing::Test {
protected:
    FlagEvaluator evaluator;

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
        return flag;
    }

    EvalContext make_context(const std::string& user_id = "user_123") {
        EvalContext ctx;
        ctx.user_id = user_id;
        return ctx;
    }
};

// ---- Basic evaluation ----

TEST_F(EvaluatorTest, DisabledFlagReturnsDefault) {
    auto flag = make_flag("feature_x", false);
    auto ctx = make_context();

    auto result = evaluator.evaluate(flag, ctx);

    EXPECT_EQ(result.key, "feature_x");
    EXPECT_FALSE(result.enabled);
    EXPECT_EQ(result.value, "false");
    EXPECT_EQ(result.reason, "disabled");
}

TEST_F(EvaluatorTest, EnabledFlagWithFullRollout) {
    auto flag = make_flag("feature_y", true, 100.0);
    auto ctx = make_context();

    auto result = evaluator.evaluate(flag, ctx);

    EXPECT_EQ(result.key, "feature_y");
    EXPECT_TRUE(result.enabled);
    EXPECT_EQ(result.value, "true");
    EXPECT_EQ(result.reason, "rule_match");
}

TEST_F(EvaluatorTest, EnabledFlagWithZeroRollout) {
    auto flag = make_flag("feature_z", true, 0.0);
    auto ctx = make_context();

    auto result = evaluator.evaluate(flag, ctx);

    EXPECT_FALSE(result.enabled);
    EXPECT_EQ(result.value, "false");
    EXPECT_EQ(result.reason, "rollout");
}

TEST_F(EvaluatorTest, EmptyUserIdReturnsError) {
    auto flag = make_flag("feature_a", true);
    EvalContext ctx; // empty user_id and anonymous_id

    auto result = evaluator.evaluate(flag, ctx);

    EXPECT_FALSE(result.enabled);
    EXPECT_EQ(result.reason, "error");
}

TEST_F(EvaluatorTest, FallsBackToAnonymousId) {
    auto flag = make_flag("feature_b", true);
    EvalContext ctx;
    ctx.anonymous_id = "anon_456";

    auto result = evaluator.evaluate(flag, ctx);

    EXPECT_TRUE(result.enabled);
}

// ---- Rollout consistency ----

TEST_F(EvaluatorTest, RolloutIsConsistent) {
    auto flag = make_flag("rollout_test", true, 50.0);
    auto ctx = make_context("consistent_user");

    // Same user should always get the same result
    auto result1 = evaluator.evaluate(flag, ctx);
    auto result2 = evaluator.evaluate(flag, ctx);
    auto result3 = evaluator.evaluate(flag, ctx);

    EXPECT_EQ(result1.enabled, result2.enabled);
    EXPECT_EQ(result2.enabled, result3.enabled);
}

TEST_F(EvaluatorTest, RolloutDistribution) {
    auto flag = make_flag("distribution_test", true, 50.0);

    int in_count = 0;
    int total = 1000;

    for (int i = 0; i < total; ++i) {
        auto ctx = make_context("user_" + std::to_string(i));
        auto result = evaluator.evaluate(flag, ctx);
        if (result.enabled) ++in_count;
    }

    // With 1000 users and 50% rollout, we expect roughly 500 in the rollout.
    // Allow generous margin for hash distribution: 35-65%
    double ratio = static_cast<double>(in_count) / total;
    EXPECT_GT(ratio, 0.35) << "Rollout ratio too low: " << ratio;
    EXPECT_LT(ratio, 0.65) << "Rollout ratio too high: " << ratio;
}

// ---- Targeting rules ----

TEST_F(EvaluatorTest, NoRulesMatchesEveryone) {
    auto flag = make_flag("no_rules", true);
    flag.rules_json = "[]";
    auto ctx = make_context();

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_TRUE(result.enabled);
}

TEST_F(EvaluatorTest, EqualsRuleMatches) {
    auto flag = make_flag("rule_test", true);
    flag.rules_json = R"([{"attribute": "plan", "operator": "equals", "value": "pro"}])";

    auto ctx = make_context();
    ctx.attributes["plan"] = "pro";

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_TRUE(result.enabled);
    EXPECT_EQ(result.reason, "rule_match");
}

TEST_F(EvaluatorTest, EqualsRuleDoesNotMatch) {
    auto flag = make_flag("rule_test", true);
    flag.rules_json = R"([{"attribute": "plan", "operator": "equals", "value": "pro"}])";

    auto ctx = make_context();
    ctx.attributes["plan"] = "free";

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_FALSE(result.enabled);
    EXPECT_EQ(result.reason, "rule_no_match");
}

TEST_F(EvaluatorTest, ContainsOperator) {
    auto flag = make_flag("contains_test", true);
    flag.rules_json = R"([{"attribute": "email", "operator": "contains", "value": "@company.com"}])";

    auto ctx = make_context();
    ctx.attributes["email"] = "alice@company.com";

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_TRUE(result.enabled);
}

TEST_F(EvaluatorTest, InOperator) {
    auto flag = make_flag("in_test", true);
    flag.rules_json = R"([{"attribute": "country", "operator": "in", "value": ["US", "CA", "UK"]}])";

    EvalContext ctx;
    ctx.user_id = "user_1";
    ctx.attributes["country"] = "CA";

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_TRUE(result.enabled);
}

TEST_F(EvaluatorTest, InOperatorNoMatch) {
    auto flag = make_flag("in_test", true);
    flag.rules_json = R"([{"attribute": "country", "operator": "in", "value": ["US", "CA", "UK"]}])";

    EvalContext ctx;
    ctx.user_id = "user_1";
    ctx.attributes["country"] = "DE";

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_FALSE(result.enabled);
}

TEST_F(EvaluatorTest, NumericGtOperator) {
    auto flag = make_flag("gt_test", true);
    flag.rules_json = R"([{"attribute": "age", "operator": "gt", "value": 18}])";

    EvalContext ctx;
    ctx.user_id = "user_1";
    ctx.attributes["age"] = "25";

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_TRUE(result.enabled);
}

TEST_F(EvaluatorTest, ConditionsAndLogic) {
    auto flag = make_flag("and_test", true);
    flag.rules_json = R"([{
        "conditions": [
            {"attribute": "plan", "operator": "equals", "value": "pro"},
            {"attribute": "country", "operator": "equals", "value": "US"}
        ]
    }])";

    EvalContext ctx;
    ctx.user_id = "user_1";
    ctx.attributes["plan"] = "pro";
    ctx.attributes["country"] = "US";

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_TRUE(result.enabled);

    // Fails if only one condition matches
    ctx.attributes["country"] = "DE";
    result = evaluator.evaluate(flag, ctx);
    EXPECT_FALSE(result.enabled);
}

TEST_F(EvaluatorTest, RulesOrLogic) {
    auto flag = make_flag("or_test", true);
    flag.rules_json = R"([
        {"attribute": "plan", "operator": "equals", "value": "pro"},
        {"attribute": "plan", "operator": "equals", "value": "enterprise"}
    ])";

    EvalContext ctx;
    ctx.user_id = "user_1";
    ctx.attributes["plan"] = "enterprise";

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_TRUE(result.enabled);
}

TEST_F(EvaluatorTest, StartsWithOperator) {
    auto flag = make_flag("prefix_test", true);
    flag.rules_json = R"([{"attribute": "email", "operator": "starts_with", "value": "admin"}])";

    EvalContext ctx;
    ctx.user_id = "user_1";
    ctx.attributes["email"] = "admin@example.com";

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_TRUE(result.enabled);
}

TEST_F(EvaluatorTest, MissingAttributeDoesNotMatch) {
    auto flag = make_flag("missing_attr", true);
    flag.rules_json = R"([{"attribute": "nonexistent", "operator": "equals", "value": "foo"}])";

    auto ctx = make_context();
    // ctx.attributes does not have "nonexistent"

    auto result = evaluator.evaluate(flag, ctx);
    EXPECT_FALSE(result.enabled);
}

// ---- Multi-variate flags ----

TEST_F(EvaluatorTest, MultiVariateSelection) {
    auto flag = make_flag("multivar", true);
    flag.variant_type = "string";
    flag.variants_json = R"([
        {"key": "control", "value": "control", "weight": 50},
        {"key": "variant_a", "value": "variant_a", "weight": 50}
    ])";

    auto ctx = make_context("user_for_variant");
    auto result = evaluator.evaluate(flag, ctx);

    EXPECT_TRUE(result.enabled);
    // Should be one of the two variants
    EXPECT_TRUE(result.value == "control" || result.value == "variant_a")
        << "Got unexpected variant: " << result.value;
}

TEST_F(EvaluatorTest, MultiVariateConsistency) {
    auto flag = make_flag("multivar_consistent", true);
    flag.variant_type = "string";
    flag.variants_json = R"([
        {"key": "a", "value": "a", "weight": 33},
        {"key": "b", "value": "b", "weight": 33},
        {"key": "c", "value": "c", "weight": 34}
    ])";

    auto ctx = make_context("stable_user");

    auto r1 = evaluator.evaluate(flag, ctx);
    auto r2 = evaluator.evaluate(flag, ctx);
    auto r3 = evaluator.evaluate(flag, ctx);

    EXPECT_EQ(r1.value, r2.value);
    EXPECT_EQ(r2.value, r3.value);
}

// ---- EvaluateAll ----

TEST_F(EvaluatorTest, EvaluateAllFlags) {
    std::vector<FlagConfig> flags;
    flags.push_back(make_flag("feature_1", true));
    flags.push_back(make_flag("feature_2", false));
    flags.push_back(make_flag("feature_3", true));

    auto ctx = make_context();
    auto results = evaluator.evaluateAll(flags, ctx);

    ASSERT_EQ(results.size(), 3u);
    EXPECT_TRUE(results[0].enabled);
    EXPECT_FALSE(results[1].enabled);
    EXPECT_TRUE(results[2].enabled);
}

} // namespace test
} // namespace apdl
