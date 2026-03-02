#pragma once
#include <string>
#include "rapidjson/document.h"

namespace apdl {
std::string get_string(const rapidjson::Value& obj, const char* key, const std::string& default_val = "");
int64_t get_int(const rapidjson::Value& obj, const char* key, int64_t default_val = 0);
double get_double(const rapidjson::Value& obj, const char* key, double default_val = 0.0);
bool has_field(const rapidjson::Value& obj, const char* key);
std::string serialize(const rapidjson::Value& val);
}
