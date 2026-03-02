"""APDL Query Service — FastAPI application entry point."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clickhouse.client import ClickHouseClient
from app.routers import events, funnels, cohorts, retention, experiments

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage startup/shutdown of shared resources."""
    client = ClickHouseClient()
    await client.connect()
    application.state.ch_client = client
    logger.info("ClickHouse connection pool initialized")
    yield
    await client.close()
    logger.info("ClickHouse connection pool closed")


app = FastAPI(
    title="APDL Query Service",
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
app.include_router(funnels.router)
app.include_router(cohorts.router)
app.include_router(retention.router)
app.include_router(experiments.router)


@app.get("/health")
async def health_check():
    """Liveness probe — returns 200 if the service is running."""
    return {"status": "ok", "service": "apdl-query"}


@app.get("/ready")
async def readiness_check():
    """Readiness probe — verifies ClickHouse connectivity."""
    try:
        client: ClickHouseClient = app.state.ch_client
        await client.execute("SELECT 1", {})
        return {"status": "ready"}
    except Exception as exc:
        logger.error("Readiness check failed: %s", exc)
        return {"status": "not_ready", "error": str(exc)}
