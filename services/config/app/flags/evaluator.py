"""Flag evaluation engine.

Implements FNV-1a hashing for consistent bucketing, percentage-based rollouts,
multi-variate selection, and rule-based targeting. Byte-identical to the C++
FlagEvaluator for hash bucketing.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

_UINT32_MAX = 0xFFFFFFFF


def hash_bucket(key: str, user_id: str) -> int:
    """FNV-1a 32-bit hash for consistent bucketing.

    Deterministic and uniform distribution, suitable for percentage-based
    rollouts and experiment variant assignment.
    """
    data = f"{key}:{user_id}"
    h = 2166136261
    for c in data.encode("utf-8"):
        h ^= c
        h = (h * 16777619) & _UINT32_MAX
    return h


def is_in_rollout(flag_key: str, user_id: str, percentage: float) -> bool:
    """Check if a user falls within the rollout percentage."""
    if percentage >= 100.0:
        return True
    if percentage <= 0.0:
        return False
    h = hash_bucket(flag_key, user_id)
    bucket = (h / _UINT32_MAX) * 100.0
    return bucket < percentage


def select_variant(flag_key: str, user_id: str, variants_json: str) -> str:
    """Select a variant based on user hash and variant weights.

    Matches C++ selectVariant exactly.
    """
    try:
        variants = json.loads(variants_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    if not isinstance(variants, list) or len(variants) == 0:
        return ""

    # Calculate total weight
    total_weight = 0.0
    for v in variants:
        if isinstance(v, dict) and "weight" in v:
            total_weight += float(v["weight"])

    if total_weight <= 0.0:
        # Equal weights if none specified
        total_weight = float(len(variants))

    h = hash_bucket(flag_key + ":variant", user_id)
    bucket = (h / _UINT32_MAX) * total_weight

    cumulative = 0.0
    for i, v in enumerate(variants):
        if not isinstance(v, dict):
            continue

        weight = 1.0  # default equal weight
        if "weight" in v and isinstance(v["weight"], (int, float)):
            weight = float(v["weight"])

        cumulative += weight
        if bucket < cumulative:
            # Return this variant's value
            if "value" in v:
                val = v["value"]
                if isinstance(val, str):
                    return val
                # Serialize non-string values (match C++ behavior)
                return json.dumps(val, separators=(",", ":"))
            if "key" in v and isinstance(v["key"], str):
                return v["key"]
            return str(i)

    # Fallback (should not happen with proper weights)
    return ""


def matches_condition(condition: dict, ctx: dict) -> bool:
    """Check a single rule condition against the evaluation context.

    Supports all operators from the C++ implementation including aliases.
    """
    if not isinstance(condition, dict):
        return False

    attribute = condition.get("attribute")
    op = condition.get("operator")
    if not isinstance(attribute, str) or not isinstance(op, str):
        return False

    # Look up the attribute value from the context
    if attribute in ("user_id", "userId"):
        actual_value = ctx.get("user_id", "")
    elif attribute in ("anonymous_id", "anonymousId"):
        actual_value = ctx.get("anonymous_id", "")
    else:
        attributes = ctx.get("attributes", {})
        if attribute in attributes:
            actual_value = attributes[attribute]
        else:
            # Attribute not found -- most operators should not match
            if op in ("not_exists", "is_not_set"):
                return True
            return False

    # Operators that don't need a value
    if "value" not in condition:
        if op in ("exists", "is_set"):
            return bool(actual_value)
        if op in ("not_exists", "is_not_set"):
            return not actual_value
        return False

    expected = condition["value"]

    # equals / eq / is
    if op in ("equals", "eq", "is"):
        if isinstance(expected, str):
            return actual_value == expected
        if isinstance(expected, bool):
            return actual_value == ("true" if expected else "false")
        if isinstance(expected, int):
            return actual_value == str(expected)
        if isinstance(expected, float):
            return actual_value == str(expected)
        return False

    # not_equals / neq / is_not
    if op in ("not_equals", "neq", "is_not"):
        if isinstance(expected, str):
            return actual_value != expected
        if isinstance(expected, bool):
            return actual_value != ("true" if expected else "false")
        return True

    # contains
    if op == "contains":
        if isinstance(expected, str):
            return expected in actual_value
        return False

    # not_contains
    if op == "not_contains":
        if isinstance(expected, str):
            return expected not in actual_value
        return True

    # starts_with
    if op == "starts_with":
        if isinstance(expected, str):
            return actual_value.startswith(expected)
        return False

    # ends_with
    if op == "ends_with":
        if isinstance(expected, str):
            return actual_value.endswith(expected)
        return False

    # in
    if op == "in":
        if isinstance(expected, list):
            for item in expected:
                if isinstance(item, str) and actual_value == item:
                    return True
        return False

    # not_in
    if op == "not_in":
        if isinstance(expected, list):
            for item in expected:
                if isinstance(item, str) and actual_value == item:
                    return False
        return True

    # Numeric comparisons: gt, gte, lt, lte
    if op in ("gt", "gte", "lt", "lte"):
        try:
            actual_num = float(actual_value)
        except (ValueError, TypeError):
            return False

        if isinstance(expected, (int, float)):
            expected_num = float(expected)
        elif isinstance(expected, str):
            try:
                expected_num = float(expected)
            except (ValueError, TypeError):
                return False
        else:
            return False

        if op == "gt":
            return actual_num > expected_num
        if op == "gte":
            return actual_num >= expected_num
        if op == "lt":
            return actual_num < expected_num
        if op == "lte":
            return actual_num <= expected_num

    # regex / matches
    if op in ("regex", "matches"):
        if isinstance(expected, str):
            try:
                return bool(re.search(expected, actual_value))
            except re.error:
                logger.warning("Invalid regex in flag rule: %s", expected)
                return False
        return False

    logger.debug("Unknown operator '%s' in flag rule", op)
    return False


def matches_rules(rules_json: str, ctx: dict) -> bool:
    """Check if the context matches any of the targeting rules.

    Rules are OR'd at the top level. Conditions within a rule are AND'd.
    Empty rules match everyone.
    """
    if not rules_json or rules_json == "[]":
        return True

    try:
        rules = json.loads(rules_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse rules JSON")
        return True  # Fail open

    if not isinstance(rules, list) or len(rules) == 0:
        return True

    for rule in rules:
        if not isinstance(rule, dict):
            continue

        # A rule has "conditions" (array of conditions, AND'd together)
        if "conditions" in rule and isinstance(rule["conditions"], list):
            conditions = rule["conditions"]
            all_match = True
            for cond in conditions:
                if not matches_condition(cond, ctx):
                    all_match = False
                    break
            if all_match:
                return True
        # Also support a flat rule that is itself a single condition
        elif "attribute" in rule and "operator" in rule:
            if matches_condition(rule, ctx):
                return True

    return False


def evaluate(flag: dict, ctx: dict) -> dict:
    """Evaluate a single flag against the given context.

    Returns a dict with keys: key, enabled, value, variant, reason.
    Reasons: "disabled", "error", "rule_no_match", "rollout", "rule_match", "default".
    """
    result = {
        "key": flag.get("key", ""),
        "enabled": False,
        "value": "",
        "variant": "",
        "reason": "",
    }

    # If flag is disabled, return the default value
    if not flag.get("enabled", False):
        result["value"] = flag.get("default_value", "false")
        result["reason"] = "disabled"
        return result

    # Determine user identifier for hashing
    user_id = ctx.get("user_id", "") or ctx.get("anonymous_id", "")
    if not user_id:
        result["value"] = flag.get("default_value", "false")
        result["reason"] = "error"
        return result

    # Check targeting rules
    if not matches_rules(flag.get("rules_json", "[]"), ctx):
        result["value"] = flag.get("default_value", "false")
        result["reason"] = "rule_no_match"
        return result

    # Check rollout percentage
    if not is_in_rollout(
        flag.get("key", ""), user_id, flag.get("rollout_percentage", 100.0)
    ):
        result["value"] = flag.get("default_value", "false")
        result["reason"] = "rollout"
        return result

    # Flag is active for this user
    result["enabled"] = True
    result["reason"] = "rule_match"

    variant_type = flag.get("variant_type", "boolean")
    variants_json = flag.get("variants_json", "[]")

    if variant_type == "boolean":
        result["value"] = "true"
    elif variants_json and variants_json != "[]":
        # Multi-variate flag -- select variant
        variant = select_variant(flag.get("key", ""), user_id, variants_json)
        if variant:
            result["value"] = variant
            result["variant"] = variant
        else:
            result["value"] = flag.get("default_value", "false")
    else:
        result["value"] = flag.get("default_value", "false")
        result["reason"] = "default"

    return result


def evaluate_all(flags: list[dict], ctx: dict) -> list[dict]:
    """Evaluate all flags against the given context."""
    return [evaluate(f, ctx) for f in flags]
