#pragma once
#include <string>
#include <unordered_map>
#include <optional>
#include "apdl/store/postgres.h"
#include "rapidjson/document.h"

namespace apdl {

// User context for flag evaluation
struct EvalContext {
    std::string user_id;
    std::string anonymous_id;
    std::unordered_map<std::string, std::string> attributes;
};

// Result of evaluating a flag
struct EvalResult {
    std::string key;
    bool enabled;
    std::string value;      // JSON-encoded value
    std::string variant;    // variant name if applicable
    std::string reason;     // "default", "rule_match", "rollout", "disabled", "error"
};

class FlagEvaluator {
public:
    FlagEvaluator() = default;

    // Evaluate a single flag against the given context
    EvalResult evaluate(const FlagConfig& flag, const EvalContext& ctx);

    // Evaluate all flags for a project against the given context
    std::vector<EvalResult> evaluateAll(const std::vector<FlagConfig>& flags, const EvalContext& ctx);

private:
    // Check if the user matches the flag's targeting rules
    bool matchesRules(const std::string& rules_json, const EvalContext& ctx);

    // Check a single rule condition
    bool matchesCondition(const rapidjson::Value& condition, const EvalContext& ctx);

    // Determine if user is in the rollout percentage using consistent hashing
    bool isInRollout(const std::string& flag_key, const std::string& user_id, double percentage);

    // Select a variant based on user hash and variant weights
    std::string selectVariant(const std::string& flag_key, const std::string& user_id,
                               const std::string& variants_json);

    // Simple hash function for consistent bucketing
    uint32_t hashBucket(const std::string& key, const std::string& user_id);
};

}
