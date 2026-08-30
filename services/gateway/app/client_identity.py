"""Canonical public client identity without trusting caller-supplied chains."""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

ProxyNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if "%" in value:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def resolve_client_ip(
    request: Request,
    trusted_proxy_cidrs: tuple[ProxyNetwork, ...],
) -> str | None:
    """Use one forwarded address only when the immediate socket peer is trusted."""
    peer_raw = request.client.host if request.client is not None else ""
    peer = _address(peer_raw)
    if peer is None:
        return None

    forwarded_values = request.headers.getlist("x-forwarded-for")
    peer_is_trusted = any(peer in network for network in trusted_proxy_cidrs)
    if peer_is_trusted and len(forwarded_values) == 1:
        forwarded = _address(forwarded_values[0])
        if forwarded is not None:
            return forwarded.compressed
    return peer.compressed
