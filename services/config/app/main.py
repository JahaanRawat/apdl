"""APDL Config Service -- FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import admin, flags, stream
from app.sse.broadcaster import SSEBroadcaster

logger = logging.getLogger(__name__)

CREATE_FLAGS_TABLE = """
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, key)
);
"""

CREATE_EXPERIMENTS_TABLE = """
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, key)
);
"""

CREATE_FLAGS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_flags_project_updated "
    "ON flags (project_id, updated_at DESC);"
)

CREATE_EXPERIMENTS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_experiments_project_updated "
    "ON experiments (project_id, updated_at DESC);"
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage startup/shutdown of shared resources."""
    # PostgreSQL connection pool
    pg_dsn = os.environ.get(
        "POSTGRES_URL",
        "postgresql://apdl:apdl_dev@localhost:5432/apdl",
    )
    pg_pool_size = int(os.environ.get("PG_POOL_SIZE", "4"))

    pg_pool = await asyncpg.create_pool(
        dsn=pg_dsn, min_size=2, max_size=pg_pool_size
    )
    logger.info("PostgreSQL connection pool initialized")

    # Initialize schema
    async with pg_pool.acquire() as conn:
        await conn.execute(CREATE_FLAGS_TABLE)
        await conn.execute(CREATE_EXPERIMENTS_TABLE)
        await conn.execute(CREATE_FLAGS_INDEX)
        await conn.execute(CREATE_EXPERIMENTS_INDEX)
    logger.info("Database schema initialized")

    # Redis
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = aioredis.from_url(redis_url)
    logger.info("Redis connection initialized")

    # SSE Broadcaster
    broadcaster = SSEBroadcaster()
    await broadcaster.start()
    logger.info("SSE broadcaster started")

    # Store in app state
    application.state.pg_pool = pg_pool
    application.state.redis = redis_client
    application.state.broadcaster = broadcaster

    yield

    # Shutdown
    await broadcaster.stop()
    logger.info("SSE broadcaster stopped")

    await redis_client.aclose()
    logger.info("Redis connection closed")

    await pg_pool.close()
    logger.info("PostgreSQL connection pool closed")


app = FastAPI(
    title="APDL Config Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(flags.router)
app.include_router(stream.router)
app.include_router(admin.router)


@app.get("/health")
async def health_check():
    """Liveness/readiness probe -- checks PG, Redis, and SSE connection count."""
    status = {"status": "ok", "service": "apdl-config"}

    try:
        async with app.state.pg_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        status["postgres"] = "ok"
    except Exception as exc:
        logger.error("Health check: PostgreSQL error: %s", exc)
        status["postgres"] = "error"
        status["status"] = "degraded"

    try:
        await app.state.redis.ping()
        status["redis"] = "ok"
    except Exception as exc:
        logger.error("Health check: Redis error: %s", exc)
        status["redis"] = "error"
        status["status"] = "degraded"

    status["sse_connections"] = await app.state.broadcaster.total_connection_count()

    return status
