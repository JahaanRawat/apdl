#include "apdl/store/postgres.h"
#include "spdlog/spdlog.h"

#include <cstring>
#include <condition_variable>

namespace apdl {

// ---- PooledConnection ----

PooledConnection::~PooledConnection() {
    if (conn_) {
        // Reset connection if it's in a bad state
        if (!conn_->isValid()) {
            conn_->reset();
        }
        store_.releaseConnection(conn_);
    }
}

// ---- PostgresStore ----

static const char* CREATE_FLAGS_TABLE = R"SQL(
    CREATE TABLE IF NOT EXISTS flags (
        key TEXT NOT NULL,
        project_id TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT false,
        description TEXT NOT NULL DEFAULT '',
        variant_type TEXT NOT NULL DEFAULT 'boolean',
        default_value TEXT NOT NULL DEFAULT 'false',
        rules_json TEXT NOT NULL DEFAULT '[]',
        variants_json TEXT NOT NULL DEFAULT '[]',
        rollout_percentage DOUBLE PRECISION NOT NULL DEFAULT 100.0,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        PRIMARY KEY (project_id, key)
    );
)SQL";

static const char* CREATE_EXPERIMENTS_TABLE = R"SQL(
    CREATE TABLE IF NOT EXISTS experiments (
        key TEXT NOT NULL,
        project_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        description TEXT NOT NULL DEFAULT '',
        variants_json TEXT NOT NULL DEFAULT '[]',
        targeting_rules_json TEXT NOT NULL DEFAULT '[]',
        traffic_percentage DOUBLE PRECISION NOT NULL DEFAULT 100.0,
        start_date TEXT NOT NULL DEFAULT '',
        end_date TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        PRIMARY KEY (project_id, key)
    );
)SQL";

static const char* CREATE_UPDATED_AT_INDEX_FLAGS =
    "CREATE INDEX IF NOT EXISTS idx_flags_project_updated ON flags (project_id, updated_at DESC);";

static const char* CREATE_UPDATED_AT_INDEX_EXPERIMENTS =
    "CREATE INDEX IF NOT EXISTS idx_experiments_project_updated ON experiments (project_id, updated_at DESC);";

PostgresStore::PostgresStore(const std::string& connection_string, int pool_size)
    : connection_string_(connection_string) {
    for (int i = 0; i < pool_size; ++i) {
        PGconn* raw = PQconnectdb(connection_string.c_str());
        if (!raw || PQstatus(raw) != CONNECTION_OK) {
            spdlog::error("PostgreSQL connection {} failed: {}", i,
                          raw ? PQerrorMessage(raw) : "allocation failure");
            if (raw) PQfinish(raw);
            continue;
        }

        auto conn = std::make_unique<PGConnection>(raw);
        available_connections_.push(conn.get());
        all_connections_.push_back(std::move(conn));
    }

    spdlog::info("PostgreSQL connection pool initialized with {} connections",
                  all_connections_.size());
}

PostgresStore::~PostgresStore() {
    // unique_ptrs in all_connections_ will clean up PGconn handles
    std::lock_guard<std::mutex> lock(pool_mutex_);
    while (!available_connections_.empty()) {
        available_connections_.pop();
    }
}

std::unique_ptr<PostgresStore> PostgresStore::create(const std::string& connection_string, int pool_size) {
    auto store = std::unique_ptr<PostgresStore>(new PostgresStore(connection_string, pool_size));
    if (store->all_connections_.empty()) {
        spdlog::error("Failed to create any PostgreSQL connections");
        return nullptr;
    }
    return store;
}

PGConnection* PostgresStore::acquireConnection() {
    std::unique_lock<std::mutex> lock(pool_mutex_);
    pool_cv_.wait(lock, [this]() { return !available_connections_.empty(); });

    PGConnection* conn = available_connections_.front();
    available_connections_.pop();

    // Validate the connection before returning
    if (!conn->isValid()) {
        spdlog::warn("Acquired connection is invalid, attempting reset");
        conn->reset();
        if (!conn->isValid()) {
            // Try to create a new connection
            PGconn* raw = PQconnectdb(connection_string_.c_str());
            if (raw && PQstatus(raw) == CONNECTION_OK) {
                // Replace the broken connection
                for (auto& c : all_connections_) {
                    if (c.get() == conn) {
                        c = std::make_unique<PGConnection>(raw);
                        conn = c.get();
                        break;
                    }
                }
            } else {
                spdlog::error("Failed to reconnect to PostgreSQL");
                if (raw) PQfinish(raw);
            }
        }
    }

    return conn;
}

void PostgresStore::releaseConnection(PGConnection* conn) {
    {
        std::lock_guard<std::mutex> lock(pool_mutex_);
        available_connections_.push(conn);
    }
    pool_cv_.notify_one();
}

bool PostgresStore::ping() {
    PGConnection* conn = acquireConnection();
    if (!conn) return false;

    PGresult* res = PQexec(conn->get(), "SELECT 1");
    bool ok = res && PQresultStatus(res) == PGRES_TUPLES_OK;
    if (res) PQclear(res);

    releaseConnection(conn);
    return ok;
}

bool PostgresStore::initSchema() {
    PGConnection* conn = acquireConnection();
    if (!conn) return false;

    const char* statements[] = {
        CREATE_FLAGS_TABLE,
        CREATE_EXPERIMENTS_TABLE,
        CREATE_UPDATED_AT_INDEX_FLAGS,
        CREATE_UPDATED_AT_INDEX_EXPERIMENTS
    };

    bool success = true;
    for (const char* sql : statements) {
        PGresult* res = PQexec(conn->get(), sql);
        if (!res || PQresultStatus(res) != PGRES_COMMAND_OK) {
            spdlog::error("Schema init failed: {}", res ? PQresultErrorMessage(res) : "null result");
            success = false;
        }
        if (res) PQclear(res);
        if (!success) break;
    }

    releaseConnection(conn);
    return success;
}

// ---- Flag operations ----

static FlagConfig row_to_flag(PGresult* res, int row) {
    FlagConfig flag;
    flag.key = PQgetvalue(res, row, PQfnumber(res, "key"));
    flag.project_id = PQgetvalue(res, row, PQfnumber(res, "project_id"));

    const char* enabled_str = PQgetvalue(res, row, PQfnumber(res, "enabled"));
    flag.enabled = (enabled_str && (enabled_str[0] == 't' || enabled_str[0] == 'T'));

    flag.description = PQgetvalue(res, row, PQfnumber(res, "description"));
    flag.variant_type = PQgetvalue(res, row, PQfnumber(res, "variant_type"));
    flag.default_value = PQgetvalue(res, row, PQfnumber(res, "default_value"));
    flag.rules_json = PQgetvalue(res, row, PQfnumber(res, "rules_json"));
    flag.variants_json = PQgetvalue(res, row, PQfnumber(res, "variants_json"));

    const char* rollout_str = PQgetvalue(res, row, PQfnumber(res, "rollout_percentage"));
    flag.rollout_percentage = rollout_str ? std::stod(rollout_str) : 100.0;

    flag.created_at = PQgetvalue(res, row, PQfnumber(res, "created_at"));
    flag.updated_at = PQgetvalue(res, row, PQfnumber(res, "updated_at"));

    return flag;
}

std::vector<FlagConfig> PostgresStore::getFlags(const std::string& project_id) {
    PGConnection* conn = acquireConnection();
    if (!conn) return {};

    const char* sql = "SELECT * FROM flags WHERE project_id = $1 ORDER BY key";
    const char* params[] = {project_id.c_str()};

    PGresult* res = PQexecParams(conn->get(), sql, 1, nullptr, params, nullptr, nullptr, 0);

    std::vector<FlagConfig> flags;

    if (res && PQresultStatus(res) == PGRES_TUPLES_OK) {
        int rows = PQntuples(res);
        flags.reserve(rows);
        for (int i = 0; i < rows; ++i) {
            flags.push_back(row_to_flag(res, i));
        }
    } else {
        spdlog::error("getFlags query failed: {}",
                       res ? PQresultErrorMessage(res) : "null result");
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return flags;
}

std::optional<FlagConfig> PostgresStore::getFlag(const std::string& project_id, const std::string& key) {
    PGConnection* conn = acquireConnection();
    if (!conn) return std::nullopt;

    const char* sql = "SELECT * FROM flags WHERE project_id = $1 AND key = $2";
    const char* params[] = {project_id.c_str(), key.c_str()};

    PGresult* res = PQexecParams(conn->get(), sql, 2, nullptr, params, nullptr, nullptr, 0);

    std::optional<FlagConfig> result;

    if (res && PQresultStatus(res) == PGRES_TUPLES_OK && PQntuples(res) > 0) {
        result = row_to_flag(res, 0);
    } else if (res && PQresultStatus(res) != PGRES_TUPLES_OK) {
        spdlog::error("getFlag query failed: {}", PQresultErrorMessage(res));
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return result;
}

bool PostgresStore::createFlag(const FlagConfig& flag) {
    PGConnection* conn = acquireConnection();
    if (!conn) return false;

    const char* sql = R"SQL(
        INSERT INTO flags (key, project_id, enabled, description, variant_type,
                           default_value, rules_json, variants_json, rollout_percentage)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    )SQL";

    std::string enabled_str = flag.enabled ? "true" : "false";
    std::string rollout_str = std::to_string(flag.rollout_percentage);

    const char* params[] = {
        flag.key.c_str(),
        flag.project_id.c_str(),
        enabled_str.c_str(),
        flag.description.c_str(),
        flag.variant_type.c_str(),
        flag.default_value.c_str(),
        flag.rules_json.c_str(),
        flag.variants_json.c_str(),
        rollout_str.c_str()
    };

    PGresult* res = PQexecParams(conn->get(), sql, 9, nullptr, params, nullptr, nullptr, 0);

    bool ok = res && PQresultStatus(res) == PGRES_COMMAND_OK;
    if (!ok) {
        spdlog::error("createFlag failed: {}",
                       res ? PQresultErrorMessage(res) : "null result");
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return ok;
}

bool PostgresStore::updateFlag(const FlagConfig& flag) {
    PGConnection* conn = acquireConnection();
    if (!conn) return false;

    const char* sql = R"SQL(
        UPDATE flags SET
            enabled = $3,
            description = $4,
            variant_type = $5,
            default_value = $6,
            rules_json = $7,
            variants_json = $8,
            rollout_percentage = $9,
            updated_at = NOW()
        WHERE project_id = $1 AND key = $2
    )SQL";

    std::string enabled_str = flag.enabled ? "true" : "false";
    std::string rollout_str = std::to_string(flag.rollout_percentage);

    const char* params[] = {
        flag.project_id.c_str(),
        flag.key.c_str(),
        enabled_str.c_str(),
        flag.description.c_str(),
        flag.variant_type.c_str(),
        flag.default_value.c_str(),
        flag.rules_json.c_str(),
        flag.variants_json.c_str(),
        rollout_str.c_str()
    };

    PGresult* res = PQexecParams(conn->get(), sql, 9, nullptr, params, nullptr, nullptr, 0);

    bool ok = false;
    if (res && PQresultStatus(res) == PGRES_COMMAND_OK) {
        const char* affected = PQcmdTuples(res);
        ok = (affected && std::strcmp(affected, "0") != 0);
    } else {
        spdlog::error("updateFlag failed: {}",
                       res ? PQresultErrorMessage(res) : "null result");
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return ok;
}

bool PostgresStore::deleteFlag(const std::string& project_id, const std::string& key) {
    PGConnection* conn = acquireConnection();
    if (!conn) return false;

    const char* sql = "DELETE FROM flags WHERE project_id = $1 AND key = $2";
    const char* params[] = {project_id.c_str(), key.c_str()};

    PGresult* res = PQexecParams(conn->get(), sql, 2, nullptr, params, nullptr, nullptr, 0);

    bool ok = false;
    if (res && PQresultStatus(res) == PGRES_COMMAND_OK) {
        const char* affected = PQcmdTuples(res);
        ok = (affected && std::strcmp(affected, "0") != 0);
    } else {
        spdlog::error("deleteFlag failed: {}",
                       res ? PQresultErrorMessage(res) : "null result");
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return ok;
}

// ---- Experiment operations ----

static ExperimentConfig row_to_experiment(PGresult* res, int row) {
    ExperimentConfig exp;
    exp.key = PQgetvalue(res, row, PQfnumber(res, "key"));
    exp.project_id = PQgetvalue(res, row, PQfnumber(res, "project_id"));
    exp.status = PQgetvalue(res, row, PQfnumber(res, "status"));
    exp.description = PQgetvalue(res, row, PQfnumber(res, "description"));
    exp.variants_json = PQgetvalue(res, row, PQfnumber(res, "variants_json"));
    exp.targeting_rules_json = PQgetvalue(res, row, PQfnumber(res, "targeting_rules_json"));

    const char* traffic_str = PQgetvalue(res, row, PQfnumber(res, "traffic_percentage"));
    exp.traffic_percentage = traffic_str ? std::stod(traffic_str) : 100.0;

    exp.start_date = PQgetvalue(res, row, PQfnumber(res, "start_date"));
    exp.end_date = PQgetvalue(res, row, PQfnumber(res, "end_date"));
    exp.created_at = PQgetvalue(res, row, PQfnumber(res, "created_at"));
    exp.updated_at = PQgetvalue(res, row, PQfnumber(res, "updated_at"));

    return exp;
}

std::vector<ExperimentConfig> PostgresStore::getExperiments(const std::string& project_id) {
    PGConnection* conn = acquireConnection();
    if (!conn) return {};

    const char* sql = "SELECT * FROM experiments WHERE project_id = $1 ORDER BY key";
    const char* params[] = {project_id.c_str()};

    PGresult* res = PQexecParams(conn->get(), sql, 1, nullptr, params, nullptr, nullptr, 0);

    std::vector<ExperimentConfig> experiments;

    if (res && PQresultStatus(res) == PGRES_TUPLES_OK) {
        int rows = PQntuples(res);
        experiments.reserve(rows);
        for (int i = 0; i < rows; ++i) {
            experiments.push_back(row_to_experiment(res, i));
        }
    } else {
        spdlog::error("getExperiments query failed: {}",
                       res ? PQresultErrorMessage(res) : "null result");
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return experiments;
}

std::optional<ExperimentConfig> PostgresStore::getExperiment(const std::string& project_id,
                                                               const std::string& key) {
    PGConnection* conn = acquireConnection();
    if (!conn) return std::nullopt;

    const char* sql = "SELECT * FROM experiments WHERE project_id = $1 AND key = $2";
    const char* params[] = {project_id.c_str(), key.c_str()};

    PGresult* res = PQexecParams(conn->get(), sql, 2, nullptr, params, nullptr, nullptr, 0);

    std::optional<ExperimentConfig> result;

    if (res && PQresultStatus(res) == PGRES_TUPLES_OK && PQntuples(res) > 0) {
        result = row_to_experiment(res, 0);
    } else if (res && PQresultStatus(res) != PGRES_TUPLES_OK) {
        spdlog::error("getExperiment query failed: {}", PQresultErrorMessage(res));
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return result;
}

bool PostgresStore::createExperiment(const ExperimentConfig& exp) {
    PGConnection* conn = acquireConnection();
    if (!conn) return false;

    const char* sql = R"SQL(
        INSERT INTO experiments (key, project_id, status, description, variants_json,
                                  targeting_rules_json, traffic_percentage, start_date, end_date)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    )SQL";

    std::string traffic_str = std::to_string(exp.traffic_percentage);

    const char* params[] = {
        exp.key.c_str(),
        exp.project_id.c_str(),
        exp.status.c_str(),
        exp.description.c_str(),
        exp.variants_json.c_str(),
        exp.targeting_rules_json.c_str(),
        traffic_str.c_str(),
        exp.start_date.c_str(),
        exp.end_date.c_str()
    };

    PGresult* res = PQexecParams(conn->get(), sql, 9, nullptr, params, nullptr, nullptr, 0);

    bool ok = res && PQresultStatus(res) == PGRES_COMMAND_OK;
    if (!ok) {
        spdlog::error("createExperiment failed: {}",
                       res ? PQresultErrorMessage(res) : "null result");
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return ok;
}

bool PostgresStore::updateExperiment(const ExperimentConfig& exp) {
    PGConnection* conn = acquireConnection();
    if (!conn) return false;

    const char* sql = R"SQL(
        UPDATE experiments SET
            status = $3,
            description = $4,
            variants_json = $5,
            targeting_rules_json = $6,
            traffic_percentage = $7,
            start_date = $8,
            end_date = $9,
            updated_at = NOW()
        WHERE project_id = $1 AND key = $2
    )SQL";

    std::string traffic_str = std::to_string(exp.traffic_percentage);

    const char* params[] = {
        exp.project_id.c_str(),
        exp.key.c_str(),
        exp.status.c_str(),
        exp.description.c_str(),
        exp.variants_json.c_str(),
        exp.targeting_rules_json.c_str(),
        traffic_str.c_str(),
        exp.start_date.c_str(),
        exp.end_date.c_str()
    };

    PGresult* res = PQexecParams(conn->get(), sql, 9, nullptr, params, nullptr, nullptr, 0);

    bool ok = false;
    if (res && PQresultStatus(res) == PGRES_COMMAND_OK) {
        const char* affected = PQcmdTuples(res);
        ok = (affected && std::strcmp(affected, "0") != 0);
    } else {
        spdlog::error("updateExperiment failed: {}",
                       res ? PQresultErrorMessage(res) : "null result");
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return ok;
}

bool PostgresStore::deleteExperiment(const std::string& project_id, const std::string& key) {
    PGConnection* conn = acquireConnection();
    if (!conn) return false;

    const char* sql = "DELETE FROM experiments WHERE project_id = $1 AND key = $2";
    const char* params[] = {project_id.c_str(), key.c_str()};

    PGresult* res = PQexecParams(conn->get(), sql, 2, nullptr, params, nullptr, nullptr, 0);

    bool ok = false;
    if (res && PQresultStatus(res) == PGRES_COMMAND_OK) {
        const char* affected = PQcmdTuples(res);
        ok = (affected && std::strcmp(affected, "0") != 0);
    } else {
        spdlog::error("deleteExperiment failed: {}",
                       res ? PQresultErrorMessage(res) : "null result");
    }

    if (res) PQclear(res);
    releaseConnection(conn);
    return ok;
}

} // namespace apdl
