from __future__ import annotations

import asyncio

import pytest

from app.rate_limit import FixedWindowRateLimiter


class MonotonicClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_fixed_window_rolls_over_on_monotonic_time() -> None:
    clock = MonotonicClock()
    limiter = FixedWindowRateLimiter(
        request_limit=2,
        window_seconds=10,
        max_clients=2,
        clock=clock,
    )

    assert await limiter.retry_after("192.0.2.1") == 0
    assert await limiter.retry_after("192.0.2.1") == 0
    assert await limiter.retry_after("192.0.2.1") == 10
    clock.advance(9.25)
    assert await limiter.retry_after("192.0.2.1") == 1
    clock.advance(0.75)
    assert await limiter.retry_after("192.0.2.1") == 0


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_overrun_one_client_budget() -> None:
    limiter = FixedWindowRateLimiter(
        request_limit=7,
        window_seconds=60,
        max_clients=100,
        clock=lambda: 100.0,
    )

    decisions = await asyncio.gather(
        *(limiter.retry_after("198.51.100.9") for _ in range(25))
    )

    assert decisions.count(0) == 7
    assert decisions.count(60) == 18


@pytest.mark.asyncio
async def test_new_clients_fail_closed_at_the_cardinality_bound() -> None:
    clock = MonotonicClock()
    limiter = FixedWindowRateLimiter(
        request_limit=10,
        window_seconds=30,
        max_clients=2,
        clock=clock,
    )

    assert await limiter.retry_after("192.0.2.1") == 0
    assert await limiter.retry_after("192.0.2.2") == 0
    assert await limiter.tracked_client_count() == 2
    assert await limiter.retry_after("192.0.2.3") == 30
    assert await limiter.tracked_client_count() == 2

    clock.advance(30)
    assert await limiter.retry_after("192.0.2.3") == 0
    assert await limiter.tracked_client_count() == 1
