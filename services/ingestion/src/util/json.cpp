#include "apdl/util/json.h"
#include "rapidjson/writer.h"
#include "rapidjson/stringbuffer.h"

namespace apdl {

std::string get_string(const rapidjson::Value& obj, const char* key, const std::string& default_val) {
    if (!obj.IsObject()) return default_val;
    auto it = obj.FindMember(key);
    if (it == obj.MemberEnd()) return default_val;
    if (!it->value.IsString()) return default_val;
    return std::string(it->value.GetString(), it->value.GetStringLength());
}

int64_t get_int(const rapidjson::Value& obj, const char* key, int64_t default_val) {
    if (!obj.IsObject()) return default_val;
    auto it = obj.FindMember(key);
    if (it == obj.MemberEnd()) return default_val;
    if (it->value.IsInt64()) return it->value.GetInt64();
    if (it->value.IsInt()) return it->value.GetInt();
    if (it->value.IsUint()) return static_cast<int64_t>(it->value.GetUint());
    if (it->value.IsUint64()) return static_cast<int64_t>(it->value.GetUint64());
    if (it->value.IsDouble()) return static_cast<int64_t>(it->value.GetDouble());
    return default_val;
}

double get_double(const rapidjson::Value& obj, const char* key, double default_val) {
    if (!obj.IsObject()) return default_val;
    auto it = obj.FindMember(key);
    if (it == obj.MemberEnd()) return default_val;
    if (it->value.IsDouble()) return it->value.GetDouble();
    if (it->value.IsInt()) return static_cast<double>(it->value.GetInt());
    if (it->value.IsInt64()) return static_cast<double>(it->value.GetInt64());
    if (it->value.IsUint()) return static_cast<double>(it->value.GetUint());
    if (it->value.IsUint64()) return static_cast<double>(it->value.GetUint64());
    return default_val;
}

bool has_field(const rapidjson::Value& obj, const char* key) {
    if (!obj.IsObject()) return false;
    return obj.FindMember(key) != obj.MemberEnd();
}

std::string serialize(const rapidjson::Value& val) {
    rapidjson::StringBuffer buffer;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
    val.Accept(writer);
    return std::string(buffer.GetString(), buffer.GetSize());
}

} // namespace apdl
