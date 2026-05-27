"""Working memory: bounded capacity, salience eviction, decay, TTL (TC-1.5).

Working memory is Redis-backed and intentionally *small* — the capacity bound is
the precursor to the Phase 2 Attention bottleneck, not an incidental limit. These
tests pin the three behaviours that make it mind-like: it never grows past
capacity (least-salient evicted), ``decay()`` ages salience and drops items below
the floor, and items expire by TTL. Time is driven by an injected ``FrozenClock``
so "advance time → expire" is exact, not wall-clock dependent.
"""

from __future__ import annotations

import pytest
from redis.asyncio import Redis

from brain.memory.working import WorkingMemory, WorkingMemoryItem
from helpers.clock import FrozenClock


def _item(content: str, salience: float, *, kind: str = "note") -> WorkingMemoryItem:
    return WorkingMemoryItem(content=content, salience=salience, kind=kind)


async def test_capacity_is_never_exceeded_and_least_salient_is_evicted(
    redis_client: Redis,
) -> None:
    wm = WorkingMemory(redis=redis_client, capacity=3, default_ttl_seconds=0, clock=FrozenClock())

    await wm.put(_item("a", 0.9))
    await wm.put(_item("b", 0.1))  # the least salient — first to go
    await wm.put(_item("c", 0.5))
    await wm.put(_item("d", 0.7))  # pushes over capacity 3

    assert await wm.count() == 3
    contents = [i.content for i in await wm.contents()]
    assert "b" not in contents
    assert set(contents) == {"a", "c", "d"}
    # contents come back most-salient first.
    assert contents == ["a", "d", "c"]


async def test_decay_scales_salience_then_evicts_below_floor(redis_client: Redis) -> None:
    wm = WorkingMemory(
        redis=redis_client,
        capacity=10,
        decay_factor=0.5,
        salience_floor=0.1,
        default_ttl_seconds=0,
        clock=FrozenClock(),
    )
    await wm.put(_item("durable", 0.8))
    await wm.put(_item("fading", 0.15))

    evicted = await wm.decay()

    # fading: 0.15 * 0.5 = 0.075 < 0.1 floor → evicted; durable: 0.8 * 0.5 = 0.4 stays.
    assert [i.content for i in evicted] == ["fading"]
    remaining = await wm.contents()
    assert [i.content for i in remaining] == ["durable"]
    assert remaining[0].salience == pytest.approx(0.4)


async def test_items_expire_by_ttl_against_the_clock(redis_client: Redis) -> None:
    clock = FrozenClock()
    wm = WorkingMemory(redis=redis_client, capacity=10, clock=clock)

    await wm.put(_item("ephemeral", 0.9), ttl=100.0)  # expires at t=100
    assert [i.content for i in await wm.contents()] == ["ephemeral"]

    clock.advance(101.0)  # now past expiry
    assert await wm.contents() == []
    assert await wm.count() == 0


async def test_zero_ttl_means_no_expiry(redis_client: Redis) -> None:
    clock = FrozenClock()
    wm = WorkingMemory(redis=redis_client, capacity=10, clock=clock)

    await wm.put(_item("permanent", 0.5), ttl=0)
    clock.advance(10_000.0)

    assert [i.content for i in await wm.contents()] == ["permanent"]
