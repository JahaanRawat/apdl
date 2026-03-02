#pragma once
#include <string>
#include <vector>
#include "rapidjson/document.h"

namespace apdl {

struct ValidationError {
    std::string field;
    std::string message;
};

struct ValidationResult {
    bool valid;
    std::vector<ValidationError> errors;
};

ValidationResult validate_event_batch(const rapidjson::Document& doc);
ValidationResult validate_single_event(const rapidjson::Value& event);

}
