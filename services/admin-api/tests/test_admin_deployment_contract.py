"""Static deployment contracts for the Admin API boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_admin_uvicorn_preserves_socket_peer_for_application_policy() -> None:
    dockerfile = (ROOT / "services/admin-api/Dockerfile").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    run_admin_api = makefile.split("run-admin-api:", 1)[1].split("\n\n", 1)[0]

    assert '"--no-proxy-headers"' in dockerfile
    assert "--forwarded-allow-ips" not in dockerfile
    assert "--no-proxy-headers" in run_admin_api


def test_compose_exposes_admin_api_on_the_configured_bind_address() -> None:
    compose = (ROOT / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert (
        "APDL_ADMIN_TRUSTED_PROXY_CIDRS: "
        "'${APDL_ADMIN_TRUSTED_PROXY_CIDRS:-[]}'"
        in compose
    )
    assert (
        '"${APDL_BIND_ADDRESS:-127.0.0.1}:'
        '${APDL_ADMIN_API_HOST_PORT:-8085}:8085"'
        in compose
    )
    assert "admin-edge:" not in compose
    assert "APDL_ADMIN_TRUSTED_PROXY_CIDRS=[]" in environment


def test_compose_fails_closed_while_local_example_enables_bounded_onboarding() -> None:
    compose = (ROOT / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert (
        "APDL_ADMIN_REGISTRATION_ENABLED: "
        "${APDL_ADMIN_REGISTRATION_ENABLED:-false}"
    ) in compose
    assert "APDL_ADMIN_MAX_ACCOUNTS: ${APDL_ADMIN_MAX_ACCOUNTS:-100}" in compose
    assert (
        "APDL_ADMIN_MAX_PROJECTS_PER_USER: "
        "${APDL_ADMIN_MAX_PROJECTS_PER_USER:-5}"
    ) in compose
    assert "APDL_BIND_ADDRESS=127.0.0.1" in environment
    assert "APDL_ADMIN_REGISTRATION_ENABLED=true" in environment
    assert "APDL_ADMIN_MAX_ACCOUNTS=100" in environment
    assert "APDL_ADMIN_MAX_PROJECTS_PER_USER=5" in environment
    assert "APDL_DEV_API_KEY=" not in environment
    assert "APDL_DEV_CLIENT_KEY=" not in environment


def test_local_example_keeps_service_routing_complete_and_last() -> None:
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")
    routing = environment.split("# ── Routing", maxsplit=1)[1]

    for assignment in (
        "REDIS_URL=redis://localhost:6379",
        "POSTGRES_URL=postgresql://apdl_runtime:apdl_runtime_dev"
        "@localhost:5432/apdl",
        "CLICKHOUSE_URL=http://localhost:8123",
        "INGESTION_SERVICE_URL=http://localhost:8080",
        "CONFIG_SERVICE_URL=http://localhost:8081",
        "QUERY_SERVICE_URL=http://localhost:8082",
        "AGENTS_SERVICE_URL=http://localhost:8083",
        "CODEGEN_SERVICE_URL=http://localhost:8084",
        "ADMIN_API_URL=http://localhost:8085",
    ):
        assert assignment in routing

    assert environment.index("# ── Common") < environment.index("# ── Admin API")
    assert environment.index("# ── Admin API") < environment.index(
        "# ── Config Service"
    )
    assert environment.index("# ── Config Service") < environment.index(
        "# ── Agents Service"
    )
    assert environment.index("# ── Agents Service") < environment.index(
        "# ── Codegen Service"
    )
    assert environment.index("# ── Codegen Service") < environment.index(
        "# ── ClickHouse Writer"
    )
    assert environment.index("# ── ClickHouse Writer") < environment.index(
        "# ── Routing"
    )
    assert "ANTHROPIC_BASE_URL" not in environment
    assert "OPENAI_BASE_URL" not in environment
    assert "ANTHROPIC_BASE_URL" not in compose
    assert "OPENAI_BASE_URL" not in compose
