"""Race-safe bounded fixed-window limits for the public Admin API route."""

from __future__ import annotations

import asyncio
import heapq
import math
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _ClientWindow:
    expires_at: float
    request_count: int


class FixedWindowRateLimiter:
    """Track at most ``max_clients`` identities using a monotonic clock."""

    def __init__(
        self,
        *,
        request_limit: int,
        window_seconds: int,
        max_clients: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (request_limit, window_seconds, max_clients)
        ):
            raise ValueError("Rate-limit bounds must be positive integers")
        self.request_limit = request_limit
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._clock = clock or time.monotonic
        self._windows: dict[str, _ClientWindow] = {}
        self._expirations: list[tuple[float, str]] = []
        self._lock = asyncio.Lock()

    def _prune_expired(self, now: float) -> None:
        while self._expirations and self._expirations[0][0] <= now:
            expires_at, client_id = heapq.heappop(self._expirations)
            current = self._windows.get(client_id)
            if current is not None and current.expires_at == expires_at:
                del self._windows[client_id]

    @staticmethod
    def _retry_after(expires_at: float, now: float) -> int:
        return max(1, math.ceil(expires_at - now))

    async def retry_after(self, client_id: str) -> int:
        """Consume one request, returning zero when allowed or retry seconds."""
        now = self._clock()
        async with self._lock:
            self._prune_expired(now)
            current = self._windows.get(client_id)
            if current is None:
                if len(self._windows) >= self.max_clients:
                    return self._retry_after(self._expirations[0][0], now)
                current = _ClientWindow(
                    expires_at=now + self.window_seconds,
                    request_count=1,
                )
                self._windows[client_id] = current
                heapq.heappush(
                    self._expirations,
                    (current.expires_at, client_id),
                )
                return 0

            if current.request_count < self.request_limit:
                current.request_count += 1
                return 0
            return self._retry_after(current.expires_at, now)

    async def tracked_client_count(self) -> int:
        """Expose bounded cardinality for operational metrics and contract tests."""
        async with self._lock:
            self._prune_expired(self._clock())
            return len(self._windows)
