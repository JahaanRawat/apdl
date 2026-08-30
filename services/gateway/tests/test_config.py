from __future__ import annotations

import ipaddress

import pytest

from app.config import GatewaySettings


REQUIRED_ENV = {
    "APDL_CONSOLE_ALLOWED_ORIGINS": '["https://console.apdl.dev"]',
}


def environment(**values: str) -> dict[str, str]:
    return {**REQUIRED_ENV, **values}


def test_defaults_are_local_and_bounded() -> None:
    settings = GatewaySettings.from_env(REQUIRED_ENV)

    assert settings.admin_api_origin == "http://admin-api:8085"
    assert settings.ingestion_origin == "http://ingestion:8080"
    assert settings.config_origin == "http://config:8081"
    assert settings.allowed_hosts == frozenset({"localhost:8000"})
    assert settings.trusted_proxy_cidrs == ()
    assert settings.console_allowed_origins == frozenset({"https://console.apdl.dev"})
    assert settings.max_event_body_bytes == 512 * 1024
    assert settings.max_request_body_bytes == 2 * 1024 * 1024
    assert settings.stream_read_timeout_seconds >= settings.read_timeout_seconds
    assert settings.api_rate_limit == 300
    assert settings.api_rate_window_seconds == 60
    assert settings.api_rate_max_clients == 10_000


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
        GatewaySettings.from_env(environment(APDL_GATEWAY_ALLOWED_HOSTS=raw))


def test_console_origin_allowlist_is_required() -> None:
    with pytest.raises(ValueError, match="APDL_CONSOLE_ALLOWED_ORIGINS is required"):
        GatewaySettings.from_env({})


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        "[]",
        '["*"]',
        '["null"]',
        '["https://*.apdl.dev"]',
        '["https://.*"]',
        '["https://.apdl.dev"]',
        '["HTTPS://console.apdl.dev"]',
        '[" https://console.apdl.dev"]',
        '["https://console.apdl.dev/"]',
        '["https://console.apdl.dev/path"]',
        '["https://console.apdl.dev?query=x"]',
        '["https://console.apdl.dev#fragment"]',
        '["https://user:secret@console.apdl.dev"]',
        '["ftp://console.apdl.dev"]',
        '["https://console.apdl.dev:443"]',
        '["https://console.apdl.dev", "https://console.apdl.dev"]',
        '["https://console_apdl.dev"]',
        '["https://console.apdl.dev:0"]',
    ],
)
def test_console_origins_reject_noncanonical_or_unsafe_values(raw: str) -> None:
    with pytest.raises(ValueError):
        GatewaySettings.from_env({"APDL_CONSOLE_ALLOWED_ORIGINS": raw})


def test_rejected_credentialed_origin_is_redacted_from_startup_error() -> None:
    with pytest.raises(ValueError) as raised:
        GatewaySettings.from_env(
            {
                "APDL_CONSOLE_ALLOWED_ORIGINS": (
                    '["https://user:credential-secret@console.apdl.dev"]'
                )
            }
        )

    assert "credential-secret" not in str(raised.value)


def test_exact_hosted_and_development_console_origins_are_configurable() -> None:
    settings = GatewaySettings.from_env(
        {
            "APDL_CONSOLE_ALLOWED_ORIGINS": (
                '["https://console.apdl.dev","http://localhost:5173",'
                '"http://[::1]:5173"]'
            )
        }
    )

    assert settings.console_allowed_origins == frozenset(
        {
            "https://console.apdl.dev",
            "http://localhost:5173",
            "http://[::1]:5173",
        }
    )


def test_hosted_origin_and_exact_host_are_configurable() -> None:
    settings = GatewaySettings.from_env(
        environment(
            ADMIN_API_URL="https://admin.internal.example",
            INGESTION_SERVICE_URL="http://ingestion.internal:8080",
            CONFIG_SERVICE_URL="http://config.internal:8081",
            APDL_GATEWAY_ALLOWED_HOSTS='["backend.example.com"]',
            APDL_GATEWAY_PUBLIC_SCHEME="https",
        )
    )

    assert settings.allowed_hosts == frozenset({"backend.example.com"})
    assert settings.public_scheme == "https"


def test_trusted_proxy_networks_are_exact_and_canonical() -> None:
    settings = GatewaySettings.from_env(
        environment(
            APDL_GATEWAY_TRUSTED_PROXY_CIDRS=(
                '["192.0.2.9/32","2001:db8::/64"]'
            )
        )
    )

    assert settings.trusted_proxy_cidrs == (
        ipaddress.ip_network("192.0.2.9/32"),
        ipaddress.ip_network("2001:db8::/64"),
    )


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "{}",
        '["192.0.2.9"]',
        '["192.0.2.9/24"]',
        '["2001:0db8::/64"]',
        '["192.0.2.9/32", "192.0.2.9/32"]',
        '["not-a-network"]',
    ],
)
def test_trusted_proxy_networks_reject_ambiguous_values(raw: str) -> None:
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDRS"):
        GatewaySettings.from_env(
            environment(APDL_GATEWAY_TRUSTED_PROXY_CIDRS=raw)
        )


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
        GatewaySettings.from_env(environment(ADMIN_API_URL=value))


def test_related_limits_are_consistent() -> None:
    with pytest.raises(ValueError, match="EVENT_BYTES"):
        GatewaySettings.from_env(
            environment(
                APDL_GATEWAY_MAX_REQUEST_BYTES="10",
                APDL_GATEWAY_MAX_EVENT_BYTES="11",
            )
        )
    with pytest.raises(ValueError, match="KEEPALIVE"):
        GatewaySettings.from_env(
            environment(
                APDL_GATEWAY_MAX_CONNECTIONS="2",
                APDL_GATEWAY_MAX_KEEPALIVE_CONNECTIONS="3",
            )
        )
    with pytest.raises(ValueError, match="STREAM_READ"):
        GatewaySettings.from_env(
            environment(
                APDL_GATEWAY_READ_TIMEOUT_SECONDS="60",
                APDL_GATEWAY_STREAM_READ_TIMEOUT_SECONDS="59",
            )
        )


@pytest.mark.parametrize(
    "name",
    [
        "APDL_GATEWAY_API_RATE_LIMIT",
        "APDL_GATEWAY_API_RATE_WINDOW_SECONDS",
        "APDL_GATEWAY_API_RATE_MAX_CLIENTS",
    ],
)
def test_rate_limit_bounds_must_be_positive_integers(name: str) -> None:
    with pytest.raises(ValueError):
        GatewaySettings.from_env(environment(**{name: "0"}))
    with pytest.raises(ValueError):
        GatewaySettings.from_env(environment(**{name: "1.5"}))
