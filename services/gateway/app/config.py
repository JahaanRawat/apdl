"""Strict runtime configuration for the unified public gateway."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

DEFAULT_MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_EVENT_BODY_BYTES = 512 * 1024
DEFAULT_API_RATE_LIMIT = 300
DEFAULT_API_RATE_WINDOW_SECONDS = 60
DEFAULT_API_RATE_MAX_CLIENTS = 10_000
_HOST_PATTERN = re.compile(
    r"^(?:localhost:\d{1,5}|"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?::\d{1,5})?)$"
)


def _positive_float(environment: Mapping[str, str], name: str, default: str) -> float:
    try:
        value = float(environment.get(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive duration")
    return value


def _positive_int(environment: Mapping[str, str], name: str, default: str) -> int:
    try:
        value = int(environment.get(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _upstream_origin(environment: Mapping[str, str], name: str, default: str) -> str:
    value = environment.get(name, default)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be one canonical HTTP(S) origin")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} has an invalid port") from exc
    return value.rstrip("/")


def _allowed_hosts(environment: Mapping[str, str]) -> frozenset[str]:
    name = "APDL_GATEWAY_ALLOWED_HOSTS"
    raw = environment.get(name, '["localhost:8000"]')
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON array") from exc
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) for item in value)
    ):
        raise ValueError(f"{name} must be a non-empty JSON string array")

    hosts: set[str] = set()
    for item in value:
        if (
            item != item.strip()
            or item != item.lower()
            or not _HOST_PATTERN.fullmatch(item)
        ):
            raise ValueError(f"Invalid gateway host: {item}")
        hostname = item.rsplit(":", 1)[0] if ":" in item else item
        port_text = item.rsplit(":", 1)[1] if ":" in item else None
        if port_text is not None and not 1 <= int(port_text) <= 65_535:
            raise ValueError(f"Invalid gateway host port: {item}")
        if hostname == "localhost" and item != "localhost:8000":
            raise ValueError("The local gateway host must be exactly localhost:8000")
        if item in hosts:
            raise ValueError(f"Duplicate gateway host: {item}")
        hosts.add(item)
    return frozenset(hosts)


def _trusted_proxy_cidrs(
    environment: Mapping[str, str],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    name = "APDL_GATEWAY_TRUSTED_PROXY_CIDRS"
    raw = environment.get(name, "[]")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON array") from exc
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{name} must be a JSON string array")

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in value:
        try:
            network = ipaddress.ip_network(item, strict=True)
        except ValueError as exc:
            raise ValueError(f"{name} contains an invalid network") from exc
        if item != str(network) or network in networks:
            raise ValueError(f"{name} must contain unique canonical networks")
        networks.append(network)
    return tuple(networks)


@dataclass(frozen=True)
class GatewaySettings:
    admin_api_origin: str
    ingestion_origin: str
    config_origin: str
    allowed_hosts: frozenset[str]
    trusted_proxy_cidrs: tuple[
        ipaddress.IPv4Network | ipaddress.IPv6Network, ...
    ]
    public_scheme: str
    max_request_body_bytes: int
    max_event_body_bytes: int
    connect_timeout_seconds: float
    read_timeout_seconds: float
    stream_read_timeout_seconds: float
    write_timeout_seconds: float
    pool_timeout_seconds: float
    max_connections: int
    max_keepalive_connections: int
    api_rate_limit: int
    api_rate_window_seconds: int
    api_rate_max_clients: int

    @classmethod
    def from_env(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "GatewaySettings":
        values = os.environ if environment is None else environment
        public_scheme = values.get("APDL_GATEWAY_PUBLIC_SCHEME", "http")
        if public_scheme not in {"http", "https"}:
            raise ValueError("APDL_GATEWAY_PUBLIC_SCHEME must be http or https")

        settings = cls(
            admin_api_origin=_upstream_origin(
                values,
                "ADMIN_API_URL",
                "http://admin-api:8085",
            ),
            ingestion_origin=_upstream_origin(
                values,
                "INGESTION_SERVICE_URL",
                "http://ingestion:8080",
            ),
            config_origin=_upstream_origin(
                values,
                "CONFIG_SERVICE_URL",
                "http://config:8081",
            ),
            allowed_hosts=_allowed_hosts(values),
            trusted_proxy_cidrs=_trusted_proxy_cidrs(values),
            public_scheme=public_scheme,
            max_request_body_bytes=_positive_int(
                values,
                "APDL_GATEWAY_MAX_REQUEST_BYTES",
                str(DEFAULT_MAX_REQUEST_BODY_BYTES),
            ),
            max_event_body_bytes=_positive_int(
                values,
                "APDL_GATEWAY_MAX_EVENT_BYTES",
                str(DEFAULT_MAX_EVENT_BODY_BYTES),
            ),
            connect_timeout_seconds=_positive_float(
                values,
                "APDL_GATEWAY_CONNECT_TIMEOUT_SECONDS",
                "5",
            ),
            read_timeout_seconds=_positive_float(
                values,
                "APDL_GATEWAY_READ_TIMEOUT_SECONDS",
                "60",
            ),
            stream_read_timeout_seconds=_positive_float(
                values,
                "APDL_GATEWAY_STREAM_READ_TIMEOUT_SECONDS",
                "3600",
            ),
            write_timeout_seconds=_positive_float(
                values,
                "APDL_GATEWAY_WRITE_TIMEOUT_SECONDS",
                "30",
            ),
            pool_timeout_seconds=_positive_float(
                values,
                "APDL_GATEWAY_POOL_TIMEOUT_SECONDS",
                "5",
            ),
            max_connections=_positive_int(
                values,
                "APDL_GATEWAY_MAX_CONNECTIONS",
                "100",
            ),
            max_keepalive_connections=_positive_int(
                values,
                "APDL_GATEWAY_MAX_KEEPALIVE_CONNECTIONS",
                "20",
            ),
            api_rate_limit=_positive_int(
                values,
                "APDL_GATEWAY_API_RATE_LIMIT",
                str(DEFAULT_API_RATE_LIMIT),
            ),
            api_rate_window_seconds=_positive_int(
                values,
                "APDL_GATEWAY_API_RATE_WINDOW_SECONDS",
                str(DEFAULT_API_RATE_WINDOW_SECONDS),
            ),
            api_rate_max_clients=_positive_int(
                values,
                "APDL_GATEWAY_API_RATE_MAX_CLIENTS",
                str(DEFAULT_API_RATE_MAX_CLIENTS),
            ),
        )
        if settings.max_event_body_bytes > settings.max_request_body_bytes:
            raise ValueError(
                "APDL_GATEWAY_MAX_EVENT_BYTES cannot exceed "
                "APDL_GATEWAY_MAX_REQUEST_BYTES"
            )
        if settings.max_keepalive_connections > settings.max_connections:
            raise ValueError(
                "APDL_GATEWAY_MAX_KEEPALIVE_CONNECTIONS cannot exceed "
                "APDL_GATEWAY_MAX_CONNECTIONS"
            )
        if settings.stream_read_timeout_seconds < settings.read_timeout_seconds:
            raise ValueError(
                "APDL_GATEWAY_STREAM_READ_TIMEOUT_SECONDS cannot be shorter than "
                "APDL_GATEWAY_READ_TIMEOUT_SECONDS"
            )
        return settings
