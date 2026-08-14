from __future__ import annotations

import pytest

from app.config import GatewaySettings


def test_defaults_are_local_and_bounded() -> None:
    settings = GatewaySettings.from_env({})

    assert settings.admin_api_origin == "http://admin-api:8085"
    assert settings.ingestion_origin == "http://ingestion:8080"
    assert settings.config_origin == "http://config:8081"
    assert settings.allowed_hosts == frozenset({"localhost:8000"})
    assert settings.max_event_body_bytes == 512 * 1024
    assert settings.max_request_body_bytes == 2 * 1024 * 1024
    assert settings.stream_read_timeout_seconds >= settings.read_timeout_seconds


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        "[]",
        '["localhost"]',
        '["localhost:8080"]',
        '["LOCALHOST:8000"]',
        '["localhost:8000", "localhost:8000"]',
        '["https://backend.example.com"]',
        '["backend.example.com/path"]',
        '["user@backend.example.com"]',
        '["backend.example.com:70000"]',
    ],
)
def test_allowed_hosts_reject_ambiguous_or_unsafe_values(raw: str) -> None:
    with pytest.raises(ValueError):
        GatewaySettings.from_env({"APDL_GATEWAY_ALLOWED_HOSTS": raw})


def test_hosted_origin_and_exact_host_are_configurable() -> None:
    settings = GatewaySettings.from_env(
        {
            "ADMIN_API_URL": "https://admin.internal.example",
            "INGESTION_SERVICE_URL": "http://ingestion.internal:8080",
            "CONFIG_SERVICE_URL": "http://config.internal:8081",
            "APDL_GATEWAY_ALLOWED_HOSTS": '["backend.example.com"]',
            "APDL_GATEWAY_PUBLIC_SCHEME": "https",
        }
    )

    assert settings.allowed_hosts == frozenset({"backend.example.com"})
    assert settings.public_scheme == "https"


@pytest.mark.parametrize(
    "value",
    [
        "admin.internal",
        "ftp://admin.internal",
        "https://user:secret@admin.internal",
        "https://admin.internal/api",
        "https://admin.internal?target=x",
        "https://admin.internal#fragment",
    ],
)
def test_upstreams_must_be_canonical_origins(value: str) -> None:
    with pytest.raises(ValueError):
        GatewaySettings.from_env({"ADMIN_API_URL": value})


def test_related_limits_are_consistent() -> None:
    with pytest.raises(ValueError, match="EVENT_BYTES"):
        GatewaySettings.from_env(
            {
                "APDL_GATEWAY_MAX_REQUEST_BYTES": "10",
                "APDL_GATEWAY_MAX_EVENT_BYTES": "11",
            }
        )
    with pytest.raises(ValueError, match="KEEPALIVE"):
        GatewaySettings.from_env(
            {
                "APDL_GATEWAY_MAX_CONNECTIONS": "2",
                "APDL_GATEWAY_MAX_KEEPALIVE_CONNECTIONS": "3",
            }
        )
    with pytest.raises(ValueError, match="STREAM_READ"):
        GatewaySettings.from_env(
            {
                "APDL_GATEWAY_READ_TIMEOUT_SECONDS": "60",
                "APDL_GATEWAY_STREAM_READ_TIMEOUT_SECONDS": "59",
            }
        )
