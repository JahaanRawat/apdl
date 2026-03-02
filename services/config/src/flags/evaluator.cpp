#include "apdl/flags/evaluator.h"
#include "spdlog/spdlog.h"
#include "rapidjson/writer.h"
#include "rapidjson/stringbuffer.h"

#include <functional>
#include <cmath>
#include <algorithm>
#include <regex>

namespace apdl {

// FNV-1a hash for consistent bucketing. This is deterministic and produces
// a uniform distribution, making it suitable for percentage-based rollouts
// and experiment variant assignment.
uint32_t FlagEvaluator::hashBucket(const std::string& key, const std::string& user_id) {
    std::string input = key + ":" + user_id;

    // FNV-1a 32-bit
    uint32_t hash = 2166136261u;
    for (char c : input) {
        hash ^= static_cast<uint32_t>(static_cast<unsigned char>(c));
        hash *= 16777619u;
    }
    return hash;
}

bool FlagEvaluator::isInRollout(const std::string& flag_key, const std::string& user_id,
                                 double percentage) {
    if (percentage >= 100.0) return true;
    if (percentage <= 0.0) return false;

    uint32_t hash = hashBucket(flag_key, user_id);
    // Map hash to 0-100 range
    double bucket = (static_cast<double>(hash) / static_cast<double>(UINT32_MAX)) * 100.0;
    return bucket < percentage;
}

std::string FlagEvaluator::selectVariant(const std::string& flag_key, const std::string& user_id,
                                          const std::string& variants_json) {
    rapidjson::Document doc;
    doc.Parse(variants_json.c_str(), variants_json.size());

    if (doc.HasParseError() || !doc.IsArray() || doc.GetArray().Size() == 0) {
        return "";
    }

    const auto& variants = doc.GetArray();

    // Calculate total weight
    double total_weight = 0.0;
    for (rapidjson::SizeType i = 0; i < variants.Size(); ++i) {
        if (variants[i].IsObject() && variants[i].HasMember("weight")) {
            total_weight += variants[i]["weight"].GetDouble();
        }
    }

    if (total_weight <= 0.0) {
        // Equal weights if none specified
        total_weight = static_cast<double>(variants.Size());
    }

    uint32_t hash = hashBucket(flag_key + ":variant", user_id);
    double bucket = (static_cast<double>(hash) / static_cast<double>(UINT32_MAX)) * total_weight;

    double cumulative = 0.0;
    for (rapidjson::SizeType i = 0; i < variants.Size(); ++i) {
        if (!variants[i].IsObject()) continue;

        double weight = 1.0; // default equal weight
        if (variants[i].HasMember("weight") && variants[i]["weight"].IsNumber()) {
            weight = variants[i]["weight"].GetDouble();
        }

        cumulative += weight;
        if (bucket < cumulative) {
            // Return this variant's value
            if (variants[i].HasMember("value")) {
                if (variants[i]["value"].IsString()) {
                    return variants[i]["value"].GetString();
                }
                // Serialize non-string values
                rapidjson::StringBuffer sb;
                rapidjson::Writer<rapidjson::StringBuffer> w(sb);
                variants[i]["value"].Accept(w);
                return std::string(sb.GetString(), sb.GetSize());
            }
            if (variants[i].HasMember("key") && variants[i]["key"].IsString()) {
                return variants[i]["key"].GetString();
            }
            return std::to_string(i);
        }
    }

    // Fallback to last variant (should not happen with proper weights)
    return "";
}

bool FlagEvaluator::matchesCondition(const rapidjson::Value& condition, const EvalContext& ctx) {
    if (!condition.IsObject()) return false;

    // A condition has: attribute, operator, value
    if (!condition.HasMember("attribute") || !condition["attribute"].IsString()) return false;
    if (!condition.HasMember("operator") || !condition["operator"].IsString()) return false;

    std::string attribute = condition["attribute"].GetString();
    std::string op = condition["operator"].GetString();

    // Look up the attribute value from the context
    std::string actual_value;
    if (attribute == "user_id" || attribute == "userId") {
        actual_value = ctx.user_id;
    } else if (attribute == "anonymous_id" || attribute == "anonymousId") {
        actual_value = ctx.anonymous_id;
    } else {
        auto it = ctx.attributes.find(attribute);
        if (it != ctx.attributes.end()) {
            actual_value = it->second;
        } else {
            // Attribute not found -- most operators should not match
            if (op == "not_exists" || op == "is_not_set") return true;
            return false;
        }
    }

    if (!condition.HasMember("value")) {
        // Some operators don't need a value (exists, not_exists)
        if (op == "exists" || op == "is_set") return !actual_value.empty();
        if (op == "not_exists" || op == "is_not_set") return actual_value.empty();
        return false;
    }

    // Get the expected value(s) from the condition
    const auto& expected = condition["value"];

    if (op == "equals" || op == "eq" || op == "is") {
        if (expected.IsString()) return actual_value == expected.GetString();
        if (expected.IsBool()) return actual_value == (expected.GetBool() ? "true" : "false");
        if (expected.IsInt()) return actual_value == std::to_string(expected.GetInt());
        if (expected.IsDouble()) return actual_value == std::to_string(expected.GetDouble());
        return false;
    }

    if (op == "not_equals" || op == "neq" || op == "is_not") {
        if (expected.IsString()) return actual_value != expected.GetString();
        if (expected.IsBool()) return actual_value != (expected.GetBool() ? "true" : "false");
        return true;
    }

    if (op == "contains") {
        if (expected.IsString()) {
            return actual_value.find(expected.GetString()) != std::string::npos;
        }
        return false;
    }

    if (op == "not_contains") {
        if (expected.IsString()) {
            return actual_value.find(expected.GetString()) == std::string::npos;
        }
        return true;
    }

    if (op == "starts_with") {
        if (expected.IsString()) {
            std::string prefix = expected.GetString();
            return actual_value.size() >= prefix.size() &&
                   actual_value.substr(0, prefix.size()) == prefix;
        }
        return false;
    }

    if (op == "ends_with") {
        if (expected.IsString()) {
            std::string suffix = expected.GetString();
            return actual_value.size() >= suffix.size() &&
                   actual_value.substr(actual_value.size() - suffix.size()) == suffix;
        }
        return false;
    }

    if (op == "in") {
        if (expected.IsArray()) {
            for (rapidjson::SizeType i = 0; i < expected.GetArray().Size(); ++i) {
                if (expected[i].IsString() && actual_value == expected[i].GetString()) {
                    return true;
                }
            }
        }
        return false;
    }

    if (op == "not_in") {
        if (expected.IsArray()) {
            for (rapidjson::SizeType i = 0; i < expected.GetArray().Size(); ++i) {
                if (expected[i].IsString() && actual_value == expected[i].GetString()) {
                    return false;
                }
            }
        }
        return true;
    }

    // Numeric comparisons
    if (op == "gt" || op == "gte" || op == "lt" || op == "lte") {
        double actual_num, expected_num;
        try {
            actual_num = std::stod(actual_value);
        } catch (...) {
            return false;
        }

        if (expected.IsNumber()) {
            expected_num = expected.GetDouble();
        } else if (expected.IsString()) {
            try {
                expected_num = std::stod(expected.GetString());
            } catch (...) {
                return false;
            }
        } else {
            return false;
        }

        if (op == "gt") return actual_num > expected_num;
        if (op == "gte") return actual_num >= expected_num;
        if (op == "lt") return actual_num < expected_num;
        if (op == "lte") return actual_num <= expected_num;
    }

    if (op == "regex" || op == "matches") {
        if (expected.IsString()) {
            try {
                std::regex re(expected.GetString());
                return std::regex_search(actual_value, re);
            } catch (const std::regex_error&) {
                spdlog::warn("Invalid regex in flag rule: {}", expected.GetString());
                return false;
            }
        }
        return false;
    }

    spdlog::debug("Unknown operator '{}' in flag rule", op);
    return false;
}

bool FlagEvaluator::matchesRules(const std::string& rules_json, const EvalContext& ctx) {
    if (rules_json.empty() || rules_json == "[]") {
        return true; // No rules means the flag applies to everyone
    }

    rapidjson::Document doc;
    doc.Parse(rules_json.c_str(), rules_json.size());

    if (doc.HasParseError() || !doc.IsArray()) {
        spdlog::warn("Failed to parse rules JSON");
        return true; // Fail open -- if rules can't be parsed, apply the flag
    }

    const auto& rules = doc.GetArray();
    if (rules.Size() == 0) return true;

    // Rules are OR'd at the top level. Each rule can have conditions that
    // are AND'd together.
    for (rapidjson::SizeType i = 0; i < rules.Size(); ++i) {
        const auto& rule = rules[i];
        if (!rule.IsObject()) continue;

        // A rule has "conditions" (array of conditions, AND'd together)
        if (rule.HasMember("conditions") && rule["conditions"].IsArray()) {
            const auto& conditions = rule["conditions"].GetArray();
            bool all_match = true;

            for (rapidjson::SizeType j = 0; j < conditions.Size(); ++j) {
                if (!matchesCondition(conditions[j], ctx)) {
                    all_match = false;
                    break;
                }
            }

            if (all_match) return true;
        }
        // Also support a flat rule that is itself a single condition
        else if (rule.HasMember("attribute") && rule.HasMember("operator")) {
            if (matchesCondition(rule, ctx)) return true;
        }
    }

    return false; // No rules matched
}

EvalResult FlagEvaluator::evaluate(const FlagConfig& flag, const EvalContext& ctx) {
    EvalResult result;
    result.key = flag.key;

    // If flag is disabled, return the default value
    if (!flag.enabled) {
        result.enabled = false;
        result.value = flag.default_value;
        result.reason = "disabled";
        return result;
    }

    // Determine user identifier for hashing
    std::string user_id = ctx.user_id.empty() ? ctx.anonymous_id : ctx.user_id;
    if (user_id.empty()) {
        result.enabled = false;
        result.value = flag.default_value;
        result.reason = "error";
        return result;
    }

    // Check targeting rules
    if (!matchesRules(flag.rules_json, ctx)) {
        result.enabled = false;
        result.value = flag.default_value;
        result.reason = "rule_no_match";
        return result;
    }

    // Check rollout percentage
    if (!isInRollout(flag.key, user_id, flag.rollout_percentage)) {
        result.enabled = false;
        result.value = flag.default_value;
        result.reason = "rollout";
        return result;
    }

    // Flag is active for this user
    result.enabled = true;
    result.reason = "rule_match";

    // Determine the value based on variant_type
    if (flag.variant_type == "boolean") {
        result.value = "true";
    } else if (!flag.variants_json.empty() && flag.variants_json != "[]") {
        // Multi-variate flag -- select variant
        std::string variant = selectVariant(flag.key, user_id, flag.variants_json);
        if (!variant.empty()) {
            result.value = variant;
            result.variant = variant;
        } else {
            result.value = flag.default_value;
        }
    } else {
        result.value = flag.default_value;
        result.reason = "default";
    }

    return result;
}

std::vector<EvalResult> FlagEvaluator::evaluateAll(const std::vector<FlagConfig>& flags,
                                                     const EvalContext& ctx) {
    std::vector<EvalResult> results;
    results.reserve(flags.size());

    for (const auto& flag : flags) {
        results.push_back(evaluate(flag, ctx));
    }

    return results;
}

} // namespace apdl
