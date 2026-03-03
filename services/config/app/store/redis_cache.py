"""Redis cache operations for flags and experiments."""

FLAGS_PREFIX = "config:flags:"
EXPERIMENTS_PREFIX = "config:experiments:"


async def get_flags(redis, project_id: str) -> str | None:
    """Return cached flags JSON for a project, or None on miss."""
    val = await redis.get(FLAGS_PREFIX + project_id)
    if val is None:
        return None
    # redis-py returns bytes; decode to str
    return val.decode("utf-8") if isinstance(val, bytes) else val


async def set_flags(redis, project_id: str, data: str, ttl: int = 60) -> None:
    """Cache flags JSON for a project with the given TTL (seconds)."""
    await redis.set(FLAGS_PREFIX + project_id, data, ex=ttl)


async def invalidate_flags(redis, project_id: str) -> None:
    """Delete cached flags for a project."""
    await redis.delete(FLAGS_PREFIX + project_id)


async def get_experiments(redis, project_id: str) -> str | None:
    """Return cached experiments JSON for a project, or None on miss."""
    val = await redis.get(EXPERIMENTS_PREFIX + project_id)
    if val is None:
        return None
    return val.decode("utf-8") if isinstance(val, bytes) else val


async def set_experiments(redis, project_id: str, data: str, ttl: int = 60) -> None:
    """Cache experiments JSON for a project with the given TTL (seconds)."""
    await redis.set(EXPERIMENTS_PREFIX + project_id, data, ex=ttl)


async def invalidate_experiments(redis, project_id: str) -> None:
    """Delete cached experiments for a project."""
    await redis.delete(EXPERIMENTS_PREFIX + project_id)
