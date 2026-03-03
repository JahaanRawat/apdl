"""asyncpg-based PostgreSQL store for flags and experiments.

All operations use parameterized queries to prevent SQL injection.
"""

import logging

logger = logging.getLogger(__name__)


def _row_to_flag(row) -> dict:
    """Convert an asyncpg Record to a flag dict."""
    return {
        "key": row["key"],
        "project_id": row["project_id"],
        "enabled": row["enabled"],
        "description": row["description"],
        "variant_type": row["variant_type"],
        "default_value": row["default_value"],
        "rules_json": row["rules_json"],
        "variants_json": row["variants_json"],
        "rollout_percentage": float(row["rollout_percentage"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _row_to_experiment(row) -> dict:
    """Convert an asyncpg Record to an experiment dict."""
    return {
        "key": row["key"],
        "project_id": row["project_id"],
        "status": row["status"],
        "description": row["description"],
        "variants_json": row["variants_json"],
        "targeting_rules_json": row["targeting_rules_json"],
        "traffic_percentage": float(row["traffic_percentage"]),
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


# ---- Flag operations ----


async def get_flags(pool, project_id: str) -> list[dict]:
    """Fetch all flags for a project, ordered by key."""
    sql = "SELECT * FROM flags WHERE project_id = $1 ORDER BY key"
    rows = await pool.fetch(sql, project_id)
    return [_row_to_flag(r) for r in rows]


async def get_flag(pool, project_id: str, key: str) -> dict | None:
    """Fetch a single flag by project_id and key."""
    sql = "SELECT * FROM flags WHERE project_id = $1 AND key = $2"
    row = await pool.fetchrow(sql, project_id, key)
    if row is None:
        return None
    return _row_to_flag(row)


async def create_flag(pool, flag: dict) -> bool:
    """Insert a new flag. Returns True on success, False on failure."""
    sql = """
        INSERT INTO flags (key, project_id, enabled, description, variant_type,
                           default_value, rules_json, variants_json, rollout_percentage)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    """
    try:
        await pool.execute(
            sql,
            flag["key"],
            flag["project_id"],
            flag.get("enabled", False),
            flag.get("description", ""),
            flag.get("variant_type", "boolean"),
            flag.get("default_value", "false"),
            flag.get("rules_json", "[]"),
            flag.get("variants_json", "[]"),
            flag.get("rollout_percentage", 100.0),
        )
        return True
    except Exception as exc:
        logger.error("createFlag failed: %s", exc)
        return False


async def update_flag(pool, flag: dict) -> bool:
    """Update an existing flag. Returns True if a row was modified."""
    sql = """
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
    """
    try:
        result = await pool.execute(
            sql,
            flag["project_id"],
            flag["key"],
            flag.get("enabled", False),
            flag.get("description", ""),
            flag.get("variant_type", "boolean"),
            flag.get("default_value", "false"),
            flag.get("rules_json", "[]"),
            flag.get("variants_json", "[]"),
            flag.get("rollout_percentage", 100.0),
        )
        # asyncpg returns e.g. "UPDATE 1" or "UPDATE 0"
        return result.endswith("1")
    except Exception as exc:
        logger.error("updateFlag failed: %s", exc)
        return False


async def delete_flag(pool, project_id: str, key: str) -> bool:
    """Delete a flag. Returns True if a row was deleted."""
    sql = "DELETE FROM flags WHERE project_id = $1 AND key = $2"
    try:
        result = await pool.execute(sql, project_id, key)
        return result.endswith("1")
    except Exception as exc:
        logger.error("deleteFlag failed: %s", exc)
        return False


# ---- Experiment operations ----


async def get_experiments(pool, project_id: str) -> list[dict]:
    """Fetch all experiments for a project, ordered by key."""
    sql = "SELECT * FROM experiments WHERE project_id = $1 ORDER BY key"
    rows = await pool.fetch(sql, project_id)
    return [_row_to_experiment(r) for r in rows]


async def get_experiment(pool, project_id: str, key: str) -> dict | None:
    """Fetch a single experiment by project_id and key."""
    sql = "SELECT * FROM experiments WHERE project_id = $1 AND key = $2"
    row = await pool.fetchrow(sql, project_id, key)
    if row is None:
        return None
    return _row_to_experiment(row)


async def create_experiment(pool, exp: dict) -> bool:
    """Insert a new experiment. Returns True on success."""
    sql = """
        INSERT INTO experiments (key, project_id, status, description, variants_json,
                                  targeting_rules_json, traffic_percentage, start_date, end_date)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    """
    try:
        await pool.execute(
            sql,
            exp["key"],
            exp["project_id"],
            exp.get("status", "draft"),
            exp.get("description", ""),
            exp.get("variants_json", "[]"),
            exp.get("targeting_rules_json", "[]"),
            exp.get("traffic_percentage", 100.0),
            exp.get("start_date", ""),
            exp.get("end_date", ""),
        )
        return True
    except Exception as exc:
        logger.error("createExperiment failed: %s", exc)
        return False


async def update_experiment(pool, exp: dict) -> bool:
    """Update an existing experiment. Returns True if a row was modified."""
    sql = """
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
    """
    try:
        result = await pool.execute(
            sql,
            exp["project_id"],
            exp["key"],
            exp.get("status", "draft"),
            exp.get("description", ""),
            exp.get("variants_json", "[]"),
            exp.get("targeting_rules_json", "[]"),
            exp.get("traffic_percentage", 100.0),
            exp.get("start_date", ""),
            exp.get("end_date", ""),
        )
        return result.endswith("1")
    except Exception as exc:
        logger.error("updateExperiment failed: %s", exc)
        return False


async def delete_experiment(pool, project_id: str, key: str) -> bool:
    """Delete an experiment. Returns True if a row was deleted."""
    sql = "DELETE FROM experiments WHERE project_id = $1 AND key = $2"
    try:
        result = await pool.execute(sql, project_id, key)
        return result.endswith("1")
    except Exception as exc:
        logger.error("deleteExperiment failed: %s", exc)
        return False
