"""Working memory — the bounded, decaying current-context buffer (Redis).

This is *not* a database table: working memory is volatile, refreshed each
cognitive tick, and intentionally **small**. The capacity bound is a feature,
not a limitation — it is the precursor to the Phase 2 Attention bottleneck
(``SPEC §15``/LIDA: flooding the context with low-salience items degrades
decisions). When full, the least-salient item is evicted; ``decay()`` ages
salience each sweep and drops items that fall below the floor or have expired.

Backing layout (one namespace, default ``wm``):
  * ``{ns}:items``    — hash of ``id -> item JSON``
  * ``{ns}:salience`` — sorted set ``id -> salience`` (eviction order)

Expiry is evaluated in Python against an injectable clock (``expires_at`` in the
payload), so "advance time → decay → expired" is deterministic in tests rather
than depending on Redis's wall-clock TTL.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import cast

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from brain.memory.base import clamp01
from foundation.config import get_settings
from foundation.redis_client import get_redis


class WorkingMemoryItem(BaseModel):
    """A single item held in the working set."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    content: str
    kind: str = "note"
    salience: float = 0.5
    created_at: float = 0.0
    # Epoch seconds after which the item is considered expired; None = no expiry.
    expires_at: float | None = None


class WorkingMemory:
    """Bounded, decaying working set over Redis.

    ``capacity``/``decay_factor``/``salience_floor``/``default_ttl`` default from
    settings but are injectable; ``redis`` and ``clock`` are injectable for tests.
    """

    def __init__(
        self,
        *,
        redis: Redis | None = None,
        capacity: int | None = None,
        decay_factor: float | None = None,
        salience_floor: float | None = None,
        default_ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.time,
        namespace: str = "wm",
    ) -> None:
        settings = get_settings()
        self._redis = redis or get_redis()
        self._capacity = capacity if capacity is not None else settings.working_memory_capacity
        self._decay_factor = (
            decay_factor if decay_factor is not None else settings.working_memory_decay_factor
        )
        self._floor = (
            salience_floor
            if salience_floor is not None
            else settings.working_memory_salience_floor
        )
        self._default_ttl = (
            default_ttl_seconds
            if default_ttl_seconds is not None
            else settings.working_memory_default_ttl_seconds
        )
        self._clock = clock
        self._items_key = f"{namespace}:items"
        self._salience_key = f"{namespace}:salience"

    # ── public API ──────────────────────────────────────────────────────────

    async def put(
        self, item: WorkingMemoryItem, ttl: float | None = None
    ) -> WorkingMemoryItem:
        """Add an item, evicting the least-salient one if at capacity.

        ``ttl`` overrides the default; ``ttl <= 0`` means no expiry. Salience is
        clamped to ``[0, 1]``. Returns the stored item (id/timestamps populated).
        """
        now = self._clock()
        effective_ttl = self._default_ttl if ttl is None else ttl
        stored = item.model_copy(
            update={
                "salience": clamp01(item.salience),
                "created_at": now,
                "expires_at": (now + effective_ttl) if effective_ttl > 0 else None,
            }
        )
        await self._prune_expired(now)
        await self._persist(stored)
        await self._trim_to_capacity()
        return stored

    async def contents(self, *, now: float | None = None) -> list[WorkingMemoryItem]:
        """Live (non-expired) items, most-salient first (recent breaks ties)."""
        reference = self._clock() if now is None else now
        await self._prune_expired(reference)
        items = await self._read_all()
        items.sort(key=lambda i: (i.salience, i.created_at), reverse=True)
        return items

    async def decay(self, *, now: float | None = None) -> list[WorkingMemoryItem]:
        """Age every item's salience by the decay factor; evict expired/sub-floor.

        Returns the items evicted by this sweep (for observability/tests).
        """
        reference = self._clock() if now is None else now
        evicted = await self._prune_expired(reference)
        for item in await self._read_all():
            decayed = item.salience * self._decay_factor
            if decayed < self._floor:
                await self._evict(item.id)
                evicted.append(item)
            else:
                item.salience = decayed
                await self._persist(item)
        return evicted

    async def count(self) -> int:
        """Number of items currently held (including not-yet-pruned expired)."""
        # redis-py types commands as a sync/async union; narrow for the async client.
        return int(await cast("Awaitable[int]", self._redis.zcard(self._salience_key)))

    async def clear(self) -> None:
        """Drop the entire working set."""
        await cast("Awaitable[int]", self._redis.delete(self._items_key, self._salience_key))

    # ── internals (reused by snapshot/restore) ───────────────────────────────

    async def _persist(self, item: WorkingMemoryItem) -> None:
        # redis-py types commands as a sync/async union; narrow for the async client.
        await cast(
            "Awaitable[int]",
            self._redis.hset(self._items_key, item.id, item.model_dump_json()),
        )
        await cast(
            "Awaitable[int]", self._redis.zadd(self._salience_key, {item.id: item.salience})
        )

    async def _evict(self, item_id: str) -> None:
        await cast("Awaitable[int]", self._redis.hdel(self._items_key, item_id))
        await cast("Awaitable[int]", self._redis.zrem(self._salience_key, item_id))

    async def _read_all(self) -> list[WorkingMemoryItem]:
        raw = await cast("Awaitable[dict[str, str]]", self._redis.hgetall(self._items_key))
        return [WorkingMemoryItem.model_validate_json(v) for v in raw.values()]

    async def _prune_expired(self, now: float) -> list[WorkingMemoryItem]:
        expired: list[WorkingMemoryItem] = []
        for item in await self._read_all():
            if item.expires_at is not None and now >= item.expires_at:
                await self._evict(item.id)
                expired.append(item)
        return expired

    async def _trim_to_capacity(self) -> None:
        """Evict the lowest-salience items until at or under capacity."""
        overflow = (
            int(await cast("Awaitable[int]", self._redis.zcard(self._salience_key)))
            - self._capacity
        )
        if overflow <= 0:
            return
        lowest = await cast(
            "Awaitable[list[str]]", self._redis.zrange(self._salience_key, 0, overflow - 1)
        )
        for item_id in lowest:
            await self._evict(item_id)
