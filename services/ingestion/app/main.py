"""APDL Ingestion Service -- FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import events

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage startup/shutdown of shared resources."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = aioredis.from_url(redis_url)
    application.state.redis = r
    logger.info("Redis connection initialized (%s)", redis_url)
    yield
    await r.aclose()
    logger.info("Redis connection closed")


app = FastAPI(
    title="APDL Ingestion Service",
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

app.include_router(events.router)


@app.get("/health")
async def health_check():
    """Liveness/readiness probe -- checks Redis connectivity."""
    try:
        await app.state.redis.ping()
        return {"status": "ok", "service": "ingestion"}
    except Exception:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "service": "ingestion"},
        )
