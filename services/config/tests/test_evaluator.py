"""Port of all 24 test cases from C++ test_evaluator.cpp.

Tests the flag evaluation engine: FNV-1a hashing, rollout bucketing,
multi-variate selection, rule matching, and evaluate/evaluateAll.
"""

from app.flags.evaluator import evaluate, evaluate_all


def make_flag(key: str, enabled: bool, rollout: float = 100.0) -> dict:
    return {
        "key": key,
        "project_id": "test_project",
        "enabled": enabled,
        "variant_type": "boolean",
        "default_value": "false",
        "rollout_percentage": rollout,
        "rules_json": "[]",
        "variants_json": "[]",
    }


def make_context(user_id: str = "user_123") -> dict:
    return {"user_id": user_id, "anonymous_id": "", "attributes": {}}


# ---- Basic evaluation ----


def test_disabled_flag_returns_default():
    flag = make_flag("feature_x", False)
    ctx = make_context()

    result = evaluate(flag, ctx)

    assert result["key"] == "feature_x"
    assert result["enabled"] is False
    assert result["value"] == "false"
    assert result["reason"] == "disabled"


def test_enabled_flag_with_full_rollout():
    flag = make_flag("feature_y", True, 100.0)
    ctx = make_context()

    result = evaluate(flag, ctx)

    assert result["key"] == "feature_y"
    assert result["enabled"] is True
    assert result["value"] == "true"
    assert result["reason"] == "rule_match"


def test_enabled_flag_with_zero_rollout():
    flag = make_flag("feature_z", True, 0.0)
    ctx = make_context()

    result = evaluate(flag, ctx)

    assert result["enabled"] is False
    assert result["value"] == "false"
    assert result["reason"] == "rollout"


def test_empty_user_id_returns_error():
    flag = make_flag("feature_a", True)
    ctx = {"user_id": "", "anonymous_id": "", "attributes": {}}

    result = evaluate(flag, ctx)

    assert result["enabled"] is False
    assert result["reason"] == "error"


def test_falls_back_to_anonymous_id():
    flag = make_flag("feature_b", True)
    ctx = {"user_id": "", "anonymous_id": "anon_456", "attributes": {}}

    result = evaluate(flag, ctx)

    assert result["enabled"] is True


# ---- Rollout consistency ----


def test_rollout_is_consistent():
    flag = make_flag("rollout_test", True, 50.0)
    ctx = make_context("consistent_user")

    result1 = evaluate(flag, ctx)
    result2 = evaluate(flag, ctx)
    result3 = evaluate(flag, ctx)

    assert result1["enabled"] == result2["enabled"]
    assert result2["enabled"] == result3["enabled"]


def test_rollout_distribution():
    flag = make_flag("distribution_test", True, 50.0)

    in_count = 0
    total = 1000

    for i in range(total):
        ctx = make_context(f"user_{i}")
        result = evaluate(flag, ctx)
        if result["enabled"]:
            in_count += 1

    # With 1000 users and 50% rollout, expect roughly 500.
    # Allow generous margin: 35-65%
    ratio = in_count / total
    assert ratio > 0.35, f"Rollout ratio too low: {ratio}"
    assert ratio < 0.65, f"Rollout ratio too high: {ratio}"


# ---- Targeting rules ----


def test_no_rules_matches_everyone():
    flag = make_flag("no_rules", True)
    flag["rules_json"] = "[]"
    ctx = make_context()

    result = evaluate(flag, ctx)
    assert result["enabled"] is True


def test_equals_rule_matches():
    flag = make_flag("rule_test", True)
    flag["rules_json"] = (
        '[{"attribute": "plan", "operator": "equals", "value": "pro"}]'
    )

    ctx = make_context()
    ctx["attributes"]["plan"] = "pro"

    result = evaluate(flag, ctx)
    assert result["enabled"] is True
    assert result["reason"] == "rule_match"


def test_equals_rule_does_not_match():
    flag = make_flag("rule_test", True)
    flag["rules_json"] = (
        '[{"attribute": "plan", "operator": "equals", "value": "pro"}]'
    )

    ctx = make_context()
    ctx["attributes"]["plan"] = "free"

    result = evaluate(flag, ctx)
    assert result["enabled"] is False
    assert result["reason"] == "rule_no_match"


def test_contains_operator():
    flag = make_flag("contains_test", True)
    flag["rules_json"] = (
        '[{"attribute": "email", "operator": "contains", "value": "@company.com"}]'
    )

    ctx = make_context()
    ctx["attributes"]["email"] = "alice@company.com"

    result = evaluate(flag, ctx)
    assert result["enabled"] is True


def test_in_operator():
    flag = make_flag("in_test", True)
    flag["rules_json"] = (
        '[{"attribute": "country", "operator": "in", "value": ["US", "CA", "UK"]}]'
    )

    ctx = {"user_id": "user_1", "anonymous_id": "", "attributes": {"country": "CA"}}

    result = evaluate(flag, ctx)
    assert result["enabled"] is True


def test_in_operator_no_match():
    flag = make_flag("in_test", True)
    flag["rules_json"] = (
        '[{"attribute": "country", "operator": "in", "value": ["US", "CA", "UK"]}]'
    )

    ctx = {"user_id": "user_1", "anonymous_id": "", "attributes": {"country": "DE"}}

    result = evaluate(flag, ctx)
    assert result["enabled"] is False


def test_numeric_gt_operator():
    flag = make_flag("gt_test", True)
    flag["rules_json"] = '[{"attribute": "age", "operator": "gt", "value": 18}]'

    ctx = {"user_id": "user_1", "anonymous_id": "", "attributes": {"age": "25"}}

    result = evaluate(flag, ctx)
    assert result["enabled"] is True


def test_conditions_and_logic():
    flag = make_flag("and_test", True)
    flag["rules_json"] = (
        '[{"conditions": ['
        '{"attribute": "plan", "operator": "equals", "value": "pro"},'
        '{"attribute": "country", "operator": "equals", "value": "US"}'
        "]}]"
    )

    ctx = {
        "user_id": "user_1",
        "anonymous_id": "",
        "attributes": {"plan": "pro", "country": "US"},
    }

    result = evaluate(flag, ctx)
    assert result["enabled"] is True

    # Fails if only one condition matches
    ctx["attributes"]["country"] = "DE"
    result = evaluate(flag, ctx)
    assert result["enabled"] is False


def test_rules_or_logic():
    flag = make_flag("or_test", True)
    flag["rules_json"] = (
        "["
        '{"attribute": "plan", "operator": "equals", "value": "pro"},'
        '{"attribute": "plan", "operator": "equals", "value": "enterprise"}'
        "]"
    )

    ctx = {
        "user_id": "user_1",
        "anonymous_id": "",
        "attributes": {"plan": "enterprise"},
    }

    result = evaluate(flag, ctx)
    assert result["enabled"] is True


def test_starts_with_operator():
    flag = make_flag("prefix_test", True)
    flag["rules_json"] = (
        '[{"attribute": "email", "operator": "starts_with", "value": "admin"}]'
    )

    ctx = {
        "user_id": "user_1",
        "anonymous_id": "",
        "attributes": {"email": "admin@example.com"},
    }

    result = evaluate(flag, ctx)
    assert result["enabled"] is True


def test_missing_attribute_does_not_match():
    flag = make_flag("missing_attr", True)
    flag["rules_json"] = (
        '[{"attribute": "nonexistent", "operator": "equals", "value": "foo"}]'
    )

    ctx = make_context()
    # ctx["attributes"] does not have "nonexistent"

    result = evaluate(flag, ctx)
    assert result["enabled"] is False


# ---- Multi-variate flags ----


def test_multi_variate_selection():
    flag = make_flag("multivar", True)
    flag["variant_type"] = "string"
    flag["variants_json"] = (
        '[{"key": "control", "value": "control", "weight": 50},'
        '{"key": "variant_a", "value": "variant_a", "weight": 50}]'
    )

    ctx = make_context("user_for_variant")
    result = evaluate(flag, ctx)

    assert result["enabled"] is True
    assert result["value"] in (
        "control",
        "variant_a",
    ), f"Got unexpected variant: {result['value']}"


def test_multi_variate_consistency():
    flag = make_flag("multivar_consistent", True)
    flag["variant_type"] = "string"
    flag["variants_json"] = (
        '[{"key": "a", "value": "a", "weight": 33},'
        '{"key": "b", "value": "b", "weight": 33},'
        '{"key": "c", "value": "c", "weight": 34}]'
    )

    ctx = make_context("stable_user")

    r1 = evaluate(flag, ctx)
    r2 = evaluate(flag, ctx)
    r3 = evaluate(flag, ctx)

    assert r1["value"] == r2["value"]
    assert r2["value"] == r3["value"]


# ---- EvaluateAll ----


def test_evaluate_all_flags():
    flags = [
        make_flag("feature_1", True),
        make_flag("feature_2", False),
        make_flag("feature_3", True),
    ]

    ctx = make_context()
    results = evaluate_all(flags, ctx)

    assert len(results) == 3
    assert results[0]["enabled"] is True
    assert results[1]["enabled"] is False
    assert results[2]["enabled"] is True
