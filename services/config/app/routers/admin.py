"""Admin CRUD endpoints for flags and experiments.

Matches the C++ admin handler behavior exactly: auth, validation,
conflict detection, cache invalidation, and SSE broadcast on mutations.
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.store import postgres as pg_store
from app.store import redis_cache
from app.utils import extract_project_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin")


def _unauthorized():
    return JSONResponse(
        status_code=401,
        content={
            "error": "unauthorized",
            "message": "API key or project_id required",
        },
    )


def _bad_json():
    return JSONResponse(
        status_code=400,
        content={"error": "bad_request", "message": "Invalid JSON body"},
    )


# ---------- Flags ----------


@router.get("/flags")
async def list_flags(request: Request):
    """List all flags for a project."""
    project_id = extract_project_id(request)
    if not project_id:
        return _unauthorized()

    pool = request.app.state.pg_pool
    flags = await pg_store.get_flags(pool, project_id)

    result_flags = []
    for f in flags:
        result_flags.append(
            {
                "key": f["key"],
                "enabled": f["enabled"],
                "description": f.get("description", ""),
                "variant_type": f.get("variant_type", "boolean"),
                "default_value": f.get("default_value", "false"),
                "rollout_percentage": f.get("rollout_percentage", 100.0),
                "created_at": f.get("created_at", ""),
                "updated_at": f.get("updated_at", ""),
            }
        )

    return JSONResponse(
        content={"flags": result_flags, "count": len(result_flags)}
    )


@router.post("/flags", status_code=201)
async def create_flag(request: Request):
    """Create a new flag. Returns 409 on duplicate, 201 on success."""
    project_id = extract_project_id(request)
    if not project_id:
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return _bad_json()

    if not isinstance(body, dict):
        return _bad_json()

    if "key" not in body or not isinstance(body["key"], str):
        return JSONResponse(
            status_code=400,
            content={
                "error": "bad_request",
                "message": "Field 'key' is required",
            },
        )

    pool = request.app.state.pg_pool

    # Check for duplicate
    existing = await pg_store.get_flag(pool, project_id, body["key"])
    if existing is not None:
        return JSONResponse(
            status_code=409,
            content={
                "error": "conflict",
                "message": f"Flag with key '{body['key']}' already exists",
            },
        )

    flag = {
        "key": body["key"],
        "project_id": project_id,
        "enabled": body.get("enabled", False)
        if isinstance(body.get("enabled"), bool)
        else False,
        "description": body.get("description", "")
        if isinstance(body.get("description"), str)
        else "",
        "variant_type": body.get("variant_type", "boolean")
        if isinstance(body.get("variant_type"), str)
        else "boolean",
        "default_value": body.get("default_value", "false")
        if isinstance(body.get("default_value"), str)
        else "false",
        "rollout_percentage": body.get("rollout_percentage", 100.0)
        if isinstance(body.get("rollout_percentage"), (int, float))
        else 100.0,
        "rules_json": json.dumps(body["rules"], separators=(",", ":"))
        if isinstance(body.get("rules"), list)
        else "[]",
        "variants_json": json.dumps(body["variants"], separators=(",", ":"))
        if isinstance(body.get("variants"), list)
        else "[]",
    }

    if not await pg_store.create_flag(pool, flag):
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "Failed to create flag in database",
            },
        )

    # Invalidate cache and broadcast
    redis = request.app.state.redis
    await redis_cache.invalidate_flags(redis, project_id)

    broadcaster = request.app.state.broadcaster
    broadcast_data = json.dumps(
        {
            "action": "flag_created",
            "key": flag["key"],
            "enabled": flag["enabled"],
        },
        separators=(",", ":"),
    )
    await broadcaster.broadcast(project_id, "flag_update", broadcast_data)

    logger.info("Flag '%s' created for project %s", flag["key"], project_id)

    return JSONResponse(
        status_code=201,
        content={"created": True, "key": flag["key"]},
    )


@router.put("/flags/{key}")
async def update_flag(key: str, request: Request):
    """Update an existing flag (partial update). Returns 404 if not found."""
    project_id = extract_project_id(request)
    if not project_id:
        return _unauthorized()

    pool = request.app.state.pg_pool

    existing = await pg_store.get_flag(pool, project_id, key)
    if existing is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "message": f"Flag '{key}' not found",
            },
        )

    try:
        body = await request.json()
    except Exception:
        return _bad_json()

    if not isinstance(body, dict):
        return _bad_json()

    # Partial update: only overwrite fields that are present in the body
    flag = dict(existing)

    if isinstance(body.get("enabled"), bool):
        flag["enabled"] = body["enabled"]
    if isinstance(body.get("description"), str):
        flag["description"] = body["description"]
    if isinstance(body.get("variant_type"), str):
        flag["variant_type"] = body["variant_type"]
    if isinstance(body.get("default_value"), str):
        flag["default_value"] = body["default_value"]
    if isinstance(body.get("rollout_percentage"), (int, float)):
        flag["rollout_percentage"] = body["rollout_percentage"]
    if isinstance(body.get("rules"), list):
        flag["rules_json"] = json.dumps(body["rules"], separators=(",", ":"))
    if isinstance(body.get("variants"), list):
        flag["variants_json"] = json.dumps(
            body["variants"], separators=(",", ":")
        )

    if not await pg_store.update_flag(pool, flag):
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "Failed to update flag",
            },
        )

    # Invalidate cache and broadcast
    redis = request.app.state.redis
    await redis_cache.invalidate_flags(redis, project_id)

    broadcaster = request.app.state.broadcaster
    broadcast_data = json.dumps(
        {
            "action": "flag_updated",
            "key": flag["key"],
            "enabled": flag["enabled"],
        },
        separators=(",", ":"),
    )
    await broadcaster.broadcast(project_id, "flag_update", broadcast_data)

    logger.info("Flag '%s' updated for project %s", flag["key"], project_id)

    return JSONResponse(
        content={"updated": True, "key": flag["key"]},
    )


@router.delete("/flags/{key}")
async def delete_flag(key: str, request: Request):
    """Delete a flag. Returns 404 if not found."""
    project_id = extract_project_id(request)
    if not project_id:
        return _unauthorized()

    pool = request.app.state.pg_pool

    if not await pg_store.delete_flag(pool, project_id, key):
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "message": f"Flag '{key}' not found or already deleted",
            },
        )

    # Invalidate cache and broadcast
    redis = request.app.state.redis
    await redis_cache.invalidate_flags(redis, project_id)

    broadcaster = request.app.state.broadcaster
    broadcast_data = json.dumps(
        {"action": "flag_deleted", "key": key},
        separators=(",", ":"),
    )
    await broadcaster.broadcast(project_id, "flag_update", broadcast_data)

    logger.info("Flag '%s' deleted for project %s", key, project_id)

    return JSONResponse(content={"deleted": True, "key": key})


# ---------- Experiments ----------


@router.get("/experiments")
async def list_experiments(request: Request):
    """List all experiments for a project."""
    project_id = extract_project_id(request)
    if not project_id:
        return _unauthorized()

    pool = request.app.state.pg_pool
    experiments = await pg_store.get_experiments(pool, project_id)

    result_exps = []
    for e in experiments:
        entry: dict = {
            "key": e["key"],
            "status": e.get("status", "draft"),
            "description": e.get("description", ""),
            "traffic_percentage": e.get("traffic_percentage", 100.0),
        }

        variants_json = e.get("variants_json", "[]")
        if variants_json and variants_json != "[]":
            entry["variants"] = json.loads(variants_json)

        entry["start_date"] = e.get("start_date", "")
        entry["end_date"] = e.get("end_date", "")
        entry["created_at"] = e.get("created_at", "")
        entry["updated_at"] = e.get("updated_at", "")
        result_exps.append(entry)

    return JSONResponse(
        content={"experiments": result_exps, "count": len(result_exps)}
    )


@router.post("/experiments", status_code=201)
async def create_experiment(request: Request):
    """Create a new experiment. Returns 409 on duplicate, 201 on success."""
    project_id = extract_project_id(request)
    if not project_id:
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return _bad_json()

    if not isinstance(body, dict):
        return _bad_json()

    if "key" not in body or not isinstance(body["key"], str):
        return JSONResponse(
            status_code=400,
            content={
                "error": "bad_request",
                "message": "Field 'key' is required",
            },
        )

    pool = request.app.state.pg_pool

    existing = await pg_store.get_experiment(pool, project_id, body["key"])
    if existing is not None:
        return JSONResponse(
            status_code=409,
            content={
                "error": "conflict",
                "message": f"Experiment with key '{body['key']}' already exists",
            },
        )

    exp = {
        "key": body["key"],
        "project_id": project_id,
        "status": body.get("status", "draft")
        if isinstance(body.get("status"), str)
        else "draft",
        "description": body.get("description", "")
        if isinstance(body.get("description"), str)
        else "",
        "traffic_percentage": body.get("traffic_percentage", 100.0)
        if isinstance(body.get("traffic_percentage"), (int, float))
        else 100.0,
        "start_date": body.get("start_date", "")
        if isinstance(body.get("start_date"), str)
        else "",
        "end_date": body.get("end_date", "")
        if isinstance(body.get("end_date"), str)
        else "",
        "variants_json": json.dumps(body["variants"], separators=(",", ":"))
        if isinstance(body.get("variants"), list)
        else "[]",
        "targeting_rules_json": json.dumps(
            body["targeting_rules"], separators=(",", ":")
        )
        if isinstance(body.get("targeting_rules"), list)
        else "[]",
    }

    if not await pg_store.create_experiment(pool, exp):
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "Failed to create experiment",
            },
        )

    # Invalidate cache and broadcast
    redis = request.app.state.redis
    await redis_cache.invalidate_experiments(redis, project_id)

    broadcaster = request.app.state.broadcaster
    broadcast_data = json.dumps(
        {
            "action": "experiment_created",
            "key": exp["key"],
            "status": exp["status"],
        },
        separators=(",", ":"),
    )
    await broadcaster.broadcast(
        project_id, "experiment_update", broadcast_data
    )

    logger.info(
        "Experiment '%s' created for project %s", exp["key"], project_id
    )

    return JSONResponse(
        status_code=201,
        content={"created": True, "key": exp["key"]},
    )


@router.put("/experiments/{key}")
async def update_experiment(key: str, request: Request):
    """Update an existing experiment (partial update). Returns 404 if not found."""
    project_id = extract_project_id(request)
    if not project_id:
        return _unauthorized()

    pool = request.app.state.pg_pool

    existing = await pg_store.get_experiment(pool, project_id, key)
    if existing is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "message": f"Experiment '{key}' not found",
            },
        )

    try:
        body = await request.json()
    except Exception:
        return _bad_json()

    if not isinstance(body, dict):
        return _bad_json()

    exp = dict(existing)

    if isinstance(body.get("status"), str):
        exp["status"] = body["status"]
    if isinstance(body.get("description"), str):
        exp["description"] = body["description"]
    if isinstance(body.get("traffic_percentage"), (int, float)):
        exp["traffic_percentage"] = body["traffic_percentage"]
    if isinstance(body.get("start_date"), str):
        exp["start_date"] = body["start_date"]
    if isinstance(body.get("end_date"), str):
        exp["end_date"] = body["end_date"]
    if isinstance(body.get("variants"), list):
        exp["variants_json"] = json.dumps(
            body["variants"], separators=(",", ":")
        )
    if isinstance(body.get("targeting_rules"), list):
        exp["targeting_rules_json"] = json.dumps(
            body["targeting_rules"], separators=(",", ":")
        )

    if not await pg_store.update_experiment(pool, exp):
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "Failed to update experiment",
            },
        )

    # Invalidate cache and broadcast
    redis = request.app.state.redis
    await redis_cache.invalidate_experiments(redis, project_id)

    broadcaster = request.app.state.broadcaster
    broadcast_data = json.dumps(
        {
            "action": "experiment_updated",
            "key": exp["key"],
            "status": exp["status"],
        },
        separators=(",", ":"),
    )
    await broadcaster.broadcast(
        project_id, "experiment_update", broadcast_data
    )

    logger.info(
        "Experiment '%s' updated for project %s", exp["key"], project_id
    )

    return JSONResponse(content={"updated": True, "key": exp["key"]})


@router.delete("/experiments/{key}")
async def delete_experiment(key: str, request: Request):
    """Delete an experiment. Returns 404 if not found."""
    project_id = extract_project_id(request)
    if not project_id:
        return _unauthorized()

    pool = request.app.state.pg_pool

    if not await pg_store.delete_experiment(pool, project_id, key):
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "message": f"Experiment '{key}' not found or already deleted",
            },
        )

    # Invalidate cache and broadcast
    redis = request.app.state.redis
    await redis_cache.invalidate_experiments(redis, project_id)

    broadcaster = request.app.state.broadcaster
    broadcast_data = json.dumps(
        {"action": "experiment_deleted", "key": key},
        separators=(",", ":"),
    )
    await broadcaster.broadcast(
        project_id, "experiment_update", broadcast_data
    )

    logger.info("Experiment '%s' deleted for project %s", key, project_id)

    return JSONResponse(content={"deleted": True, "key": key})
