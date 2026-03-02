#pragma once
#include <string>
#include <vector>
#include <optional>
#include <memory>
#include <mutex>
#include <condition_variable>
#include <queue>
#include <libpq-fe.h>

namespace apdl {

// Data structures for flags and experiments
struct FlagConfig {
    std::string key;
    std::string project_id;
    bool enabled = false;
    std::string description;
    std::string variant_type;          // "boolean", "string", "number", "json"
    std::string default_value;         // JSON-encoded default
    std::string rules_json;            // JSON array of targeting rules
    std::string variants_json;         // JSON array of variant definitions
    double rollout_percentage = 100.0;
    std::string created_at;
    std::string updated_at;
};

struct ExperimentConfig {
    std::string key;
    std::string project_id;
    std::string status;                // "draft", "running", "paused", "completed"
    std::string description;
    std::string variants_json;         // JSON array of variants with weights
    std::string targeting_rules_json;  // JSON targeting rules
    double traffic_percentage = 100.0;
    std::string start_date;
    std::string end_date;
    std::string created_at;
    std::string updated_at;
};

// Simple connection pool wrapper
class PGConnection {
public:
    explicit PGConnection(PGconn* conn) : conn_(conn) {}
    ~PGConnection() { if (conn_) PQfinish(conn_); }

    PGConnection(const PGConnection&) = delete;
    PGConnection& operator=(const PGConnection&) = delete;
    PGConnection(PGConnection&& other) noexcept : conn_(other.conn_) { other.conn_ = nullptr; }
    PGConnection& operator=(PGConnection&& other) noexcept {
        if (this != &other) {
            if (conn_) PQfinish(conn_);
            conn_ = other.conn_;
            other.conn_ = nullptr;
        }
        return *this;
    }

    PGconn* get() const { return conn_; }
    bool isValid() const { return conn_ && PQstatus(conn_) == CONNECTION_OK; }
    bool reset() {
        if (conn_) { PQreset(conn_); return isValid(); }
        return false;
    }

private:
    PGconn* conn_;
};

class PostgresStore {
public:
    static std::unique_ptr<PostgresStore> create(const std::string& connection_string, int pool_size = 4);
    ~PostgresStore();

    PostgresStore(const PostgresStore&) = delete;
    PostgresStore& operator=(const PostgresStore&) = delete;

    bool ping();
    bool initSchema();

    // Flag operations
    std::vector<FlagConfig> getFlags(const std::string& project_id);
    std::optional<FlagConfig> getFlag(const std::string& project_id, const std::string& key);
    bool createFlag(const FlagConfig& flag);
    bool updateFlag(const FlagConfig& flag);
    bool deleteFlag(const std::string& project_id, const std::string& key);

    // Experiment operations
    std::vector<ExperimentConfig> getExperiments(const std::string& project_id);
    std::optional<ExperimentConfig> getExperiment(const std::string& project_id, const std::string& key);
    bool createExperiment(const ExperimentConfig& exp);
    bool updateExperiment(const ExperimentConfig& exp);
    bool deleteExperiment(const std::string& project_id, const std::string& key);

private:
    friend class PooledConnection;

    explicit PostgresStore(const std::string& connection_string, int pool_size);

    PGConnection* acquireConnection();
    void releaseConnection(PGConnection* conn);

    std::string connection_string_;
    std::vector<std::unique_ptr<PGConnection>> all_connections_;
    std::queue<PGConnection*> available_connections_;
    std::mutex pool_mutex_;
    std::condition_variable pool_cv_;
};

// RAII connection guard
class PooledConnection {
public:
    PooledConnection(PostgresStore& store, PGConnection* conn)
        : store_(store), conn_(conn) {}
    ~PooledConnection();

    PGconn* get() const { return conn_ ? conn_->get() : nullptr; }
    bool isValid() const { return conn_ && conn_->isValid(); }

private:
    PostgresStore& store_;
    PGConnection* conn_;
    friend class PostgresStore;
};

}
