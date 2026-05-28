"""TC-6b.11 (wiring) — the cycle's run-loop actually fires due wakeups (FC-7).

``test_scheduler.py`` proves ``Scheduler.fire_due`` works when called directly, and
``test_belt.py`` proves the ``schedule_wakeup`` tool shares the cycle's Scheduler
instance. The link those leave unproven — and the one a scheduled self-prompt
depends on in production — is that the **cycle's between-ticks run-loop phase
actually invokes the scheduler**. If it didn't, a wakeup would persist + be due
forever but never wake Johnny.

``run()`` is ``tick() → _fire_due_wakeups() → _maybe_sleep() → sleep`` in a loop, so
we drive the wakeup phase directly (deterministic, no live loop) with a real
``Scheduler`` holding a due wakeup and assert it fires + injects the self-percept,
exactly once. DB-backed (``scheduled_wakeup``) → in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.agents.sensorium import MODALITY_TEXT, InputQueue
from brain.cycle import CognitiveCycle
from brain.effectors.scheduler import WAKEUP_SOURCE, Scheduler
from brain.workspace import Workspace


async def _noop_sleep(_seconds: float) -> None:
    return None


class _RecordingQueue(InputQueue):
    """Records pushes instead of touching Redis (proves the self-percept injection)."""

    def __init__(self) -> None:
        self.pushes: list[tuple[str, str]] = []

    async def push(self, raw: str, *, source: str, modality: str = MODALITY_TEXT) -> None:
        self.pushes.append((raw, source))


@pytest_asyncio.fixture
async def wakeup_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean ``scheduled_wakeup`` table on a fresh, loop-local global engine."""
    engine = install_fresh_global_engine()
    await truncate_tables(("scheduled_wakeup",))
    try:
        yield engine
    finally:
        await truncate_tables(("scheduled_wakeup",))
        await dispose_global_engine()


async def test_run_loop_phase_fires_due_wakeups_through_its_scheduler(
    wakeup_db: AsyncEngine, redis_client: Redis, frozen_clock: FrozenClock
) -> None:
    now_fn = datetime_from(frozen_clock)
    queue = _RecordingQueue()
    scheduler = Scheduler(input_queue=queue, now_fn=now_fn)
    bus = Workspace(
        redis=redis_client,
        channel=f"johnny:test:{uuid.uuid4().hex}:bus",
        contents_key=f"johnny:test:{uuid.uuid4().hex}:contents",
        now_fn=now_fn,
    )
    cycle = CognitiveCycle(bus, scheduler=scheduler, sleep_fn=_noop_sleep)

    # Johnny schedules a self-prompt; time then passes beyond its fire_at.
    await scheduler.schedule(delay_seconds=60, reason="follow up on the mars rover story")
    frozen_clock.advance(120)

    # The between-ticks run-loop phase (what run() calls after each tick) fires it.
    await cycle._fire_due_wakeups()

    # The self-percept was injected — Johnny will "remember" his intention next PERCEIVE.
    assert queue.pushes == [("follow up on the mars rover story", WAKEUP_SOURCE)]

    # And a second pass does NOT double-fire (the claim flipped it pending→fired).
    await cycle._fire_due_wakeups()
    assert len(queue.pushes) == 1


async def test_run_loop_phase_is_a_noop_before_a_wakeup_is_due(
    wakeup_db: AsyncEngine, redis_client: Redis, frozen_clock: FrozenClock
) -> None:
    now_fn = datetime_from(frozen_clock)
    queue = _RecordingQueue()
    scheduler = Scheduler(input_queue=queue, now_fn=now_fn)
    bus = Workspace(
        redis=redis_client,
        channel=f"johnny:test:{uuid.uuid4().hex}:bus",
        contents_key=f"johnny:test:{uuid.uuid4().hex}:contents",
        now_fn=now_fn,
    )
    cycle = CognitiveCycle(bus, scheduler=scheduler, sleep_fn=_noop_sleep)

    await scheduler.schedule(delay_seconds=300, reason="much later")
    frozen_clock.advance(60)  # not yet due

    await cycle._fire_due_wakeups()
    assert queue.pushes == []  # nothing injected before fire_at
