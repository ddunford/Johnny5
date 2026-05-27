"""Redis foundation: a process-wide async client + health ping.

Redis backs the Global Workspace (pub/sub) and working-memory/drive state. A
single shared client is created lazily and closed on shutdown. ``decode_responses``
is on so callers get ``str`` payloads rather than bytes.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis, from_url

from foundation.config import get_settings

_redis: Redis | None = None


def get_redis() -> Redis:
    """Return the process-wide async Redis client, creating it on first use."""
    global _redis
    if _redis is None:
        _redis = from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    """Close the Redis client (called on shutdown / in tests)."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None


async def ping() -> bool:
    """Return True if Redis answers PING."""
    # redis-py types ping() as a sync/async union; on the async client it's an
    # awaitable, so narrow it explicitly for the type checker.
    return bool(await cast("Awaitable[bool]", get_redis().ping()))
