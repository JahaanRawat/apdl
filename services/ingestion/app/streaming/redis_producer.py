"""Redis Streams publisher matching the C++ RedisProducer XADD behaviour."""

import json

STREAM_MAXLEN = 1000000


async def publish_event(redis, stream_key: str, event: dict) -> str:
    """Publish event to Redis Stream using XADD with approximate MAXLEN trimming."""
    event_json = json.dumps(event, separators=(",", ":"))
    return await redis.xadd(
        stream_key, {"event_json": event_json}, maxlen=STREAM_MAXLEN, approximate=True
    )
