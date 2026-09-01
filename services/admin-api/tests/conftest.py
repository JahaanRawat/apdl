from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import proxy
from app.auth import AdminSession, require_session
from app.config import Settings

TEST_API_KEY = "proj_demo_0123456789abcdef"


def make_settings(**overrides) -> Settings:
    values = {
        "deployment_id": "87fab7d6-dba0-4f77-8ffd-00e815fc7303",
        "display_name": "Test APDL",
        "backend_version": "0.3.4",
        "build_revision": "a" * 40,
        "postgres_url": "postgresql://test",
        "service_urls": {
            "ingestion": "http://ingestion.test",
            "config": "http://config.test",
            "query": "http://query.test",
            "agents": "http://agents.test",
            "codegen": "http://codegen.test",
            "llm-vault": "http://llm-vault.test",
        },
        "service_api_keys": {"demo": TEST_API_KEY},
        "llm_vault_admin_token": "test-vault-admin-token-is-32-bytes-long",
        "registration_enabled": True,
        "max_accounts": 100,
        "max_projects_per_user": 5,
        "session_ttl_seconds": 28_800,
        "login_risk_hmac_key": "test-admin-login-risk-key-32-bytes",
        "trusted_proxy_cidrs": (
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("::1/128"),
        ),
        "login_rate_window_seconds": 60,
        "login_global_rate_limit": 600,
        "login_network_rate_limit": 30,
        "invitation_global_rate_limit": 600,
        "invitation_network_rate_limit": 30,
        "invitation_token_rate_limit": 20,
        "login_progressive_failure_threshold": 3,
        "login_progressive_base_delay_seconds": 1,
        "login_progressive_max_delay_seconds": 60,
        "login_account_notice_threshold": 50,
        "login_account_risk_window_seconds": 86_400,
        "max_request_bytes": 2_097_152,
        "stream_authority_check_seconds": 5.0,
        "upstream_read_timeout_seconds": 60.0,
        "readiness_probe_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return Settings(**values)


class AuditConnection:
    def __init__(self, statements: list[tuple[str, tuple[object, ...]]]) -> None:
        self.statements = statements
        self.llm_connection_authorized = True
        self.project_execution_authorized = True
        self.repository_connection_authorized = True

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query: str, *args):
        self.statements.append((query, args))
        if (
            "INSERT INTO auth_credentials" in query
            and not self.project_execution_authorized
            and "agents:approve" in args[4]
        ):
            raise AssertionError(
                "unauthorized project attempted to mint agents:approve"
            )
        return "OK"

    async def fetchrow(self, query: str, *args):
        self.statements.append((query, args))
        if "AS llm_connection_authorized" in query:
            return {
                "llm_connection_authorized": self.llm_connection_authorized
            }
        if "AS project_execution_authorized" in query:
            return {
                "project_execution_authorized": (
                    self.project_execution_authorized
                )
            }
        if "AS repository_connection_authorized" in query:
            return {
                "repository_connection_authorized": (
                    self.repository_connection_authorized
                )
            }
        if "AS session_active" in query and "AS project_authorized" in query:
            return {"session_active": True, "project_authorized": True}
        raise AssertionError(f"Unexpected fetchrow query: {query}")


class AuditPool:
    def __init__(self, statements: list[tuple[str, tuple[object, ...]]]) -> None:
        self.connection = AuditConnection(statements)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


@pytest.fixture
def admin_session() -> AdminSession:
    return AdminSession(
        session_id="10000000-0000-4000-8000-000000000001",
        token_hash="a" * 64,
        deployment_id="30000000-0000-4000-8000-000000000003",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        user_id="20000000-0000-4000-8000-000000000002",
        email="admin@example.com",
        projects={
            "demo": frozenset(
                {
                    "events:write",
                    "config:read",
                    "config:write",
                    "config:evaluate",
                    "query:read",
                    "agents:read",
                    "agents:run",
                    "agents:manage",
                    "agents:approve",
                    "credentials:manage",
                }
            )
        },
    )


@asynccontextmanager
async def proxy_client(
    transport: httpx.AsyncBaseTransport,
    session: AdminSession,
    settings: Settings | None = None,
) -> AsyncIterator[TestClient]:
    app = FastAPI()
    app.state.settings = settings or make_settings()
    app.state.http_client = httpx.AsyncClient(transport=transport)
    app.state.audit_statements = []
    app.state.pg_pool = AuditPool(app.state.audit_statements)
    app.include_router(proxy.router)
    app.dependency_overrides[require_session] = lambda: session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        await app.state.http_client.aclose()
