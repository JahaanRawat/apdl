"""Token-bucket rate limiter matching the C++ RateLimitMiddleware exactly.

Uses in-memory buckets keyed by project or IP, with asyncio.Lock for
concurrency safety. Refills tokens based on elapsed wall-clock time
via time.monotonic().
"""

import asyncio
import json
import math
import time
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import Response

DEFAULT_CAPACITY = 1000.0
DEFAULT_RATE = 100.0  # tokens per second

_lock = asyncio.Lock()
_buckets: dict[str, "TokenBucket"] = {}


@dataclass
class TokenBucket:
    tokens: float = DEFAULT_CAPACITY
    last_refill: float = field(default_factory=time.monotonic)
    rate: float = DEFAULT_RATE
    capacity: float = DEFAULT_CAPACITY


async def check_rate_limit(project_id: str, request: Request) -> Response | None:
    """Check rate limit for a request. Returns None if allowed, or a 429 Response."""
    # Determine bucket key
    if project_id:
        bucket_key = f"project:{project_id}"
    else:
        client_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or request.headers.get("x-real-ip", "")
            or (request.client.host if request.client else "")
        )
        bucket_key = f"ip:{client_ip}"

    async with _lock:
        now = time.monotonic()

        if bucket_key not in _buckets:
            # Create a new bucket, consume one token immediately
            bucket = TokenBucket(
                tokens=DEFAULT_CAPACITY - 1.0,
                last_refill=now,
                rate=DEFAULT_RATE,
                capacity=DEFAULT_CAPACITY,
            )
            _buckets[bucket_key] = bucket
            return None

        bucket = _buckets[bucket_key]

        # Refill tokens based on elapsed time
        elapsed = now - bucket.last_refill
        bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.rate)
        bucket.last_refill = now

        # Try to consume one token
        if bucket.tokens < 1.0:
            # Calculate retry-after in seconds
            deficit = 1.0 - bucket.tokens
            retry_after = int(math.ceil(deficit / bucket.rate))
            if retry_after < 1:
                retry_after = 1

            return Response(
                content=json.dumps({
                    "error": "rate_limited",
                    "message": "Too many requests. Please retry after the Retry-After period.",
                }),
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(int(bucket.capacity)),
                    "X-RateLimit-Remaining": "0",
                },
            )

        bucket.tokens -= 1.0
        return None
