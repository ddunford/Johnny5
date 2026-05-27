"""TC-6b.6 — ``schedule_wakeup`` tool + the ``Scheduler`` due-check.

DB-backed (the ``scheduled_wakeup`` table), run in-network via ``./ctl.sh test``.
A recording stand-in for the Sensorium's ``InputQueue`` lets us assert the
self-percept injection deterministically, and an explicit ``now`` drives the
due-check past ``fire_at`` without the wall clock. We prove: a wakeup persists
pending; it does NOT fire before ``fire_at``; it fires once past it (status →
fired) and injects a self-percept carrying its reason; and a fired wakeup never
double-fires.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.agents.sensorium import MODALITY_TEXT, InputQueue
from brain.effectors.scheduler import (
    STATUS_FIRED,
    STATUS_PENDING,
    WAKEUP_SOURCE,
    Scheduler,
    ScheduleWakeupArgs,
    ScheduleWakeupTool,
)

_BASE = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


class _RecordingQueue(InputQueue):
    """An ``InputQueue`` stand-in that records pushes instead of touching Redis."""

    def __init__(self) -> None:
        self.pushes: list[tuple[str, str]] = []

    async def push(self, raw: str, *, source: str, modality: str = MODALITY_TEXT) -> None:
        self.pushes.append((raw, source))


class _Clock:
    """A settable now_fn for deterministic scheduling."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest_asyncio.fixture
async def scheduler_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean ``scheduled_wakeup`` table on a fresh, loop-local global engine."""
    engine = install_fresh_global_engine()
    await truncate_tables(("scheduled_wakeup",))
    try:
        yield engine
    finally:
        await truncate_tables(("scheduled_wakeup",))
        await dispose_global_engine()


async def test_schedule_persists_a_pending_wakeup(scheduler_db: AsyncEngine) -> None:
    scheduler = Scheduler(input_queue=_RecordingQueue(), now_fn=_Clock(_BASE))

    wakeup = await scheduler.schedule(delay_seconds=60, reason="check the Mars news")

    assert wakeup.id is not None
    assert wakeup.status == STATUS_PENDING
    assert wakeup.fire_at == _BASE + timedelta(seconds=60)
    pending = await scheduler.pending()
    assert [w.reason for w in pending] == ["check the Mars news"]


async def test_does_not_fire_before_fire_at(scheduler_db: AsyncEngine) -> None:
    queue = _RecordingQueue()
    scheduler = Scheduler(input_queue=queue, now_fn=_Clock(_BASE))
    await scheduler.schedule(delay_seconds=60, reason="later")

    fired = await scheduler.fire_due(now=_BASE + timedelta(seconds=30))  # not yet due

    assert fired == []
    assert queue.pushes == []
    assert len(await scheduler.pending()) == 1  # still pending


async def test_fires_and_injects_a_self_percept_when_due(scheduler_db: AsyncEngine) -> None:
    queue = _RecordingQueue()
    scheduler = Scheduler(input_queue=queue, now_fn=_Clock(_BASE))
    await scheduler.schedule(delay_seconds=60, reason="check the Mars news")

    fired = await scheduler.fire_due(now=_BASE + timedelta(seconds=120))  # past fire_at

    assert len(fired) == 1
    assert fired[0].status == STATUS_FIRED
    # The self-percept was injected onto the input queue carrying the reason.
    assert queue.pushes == [("check the Mars news", WAKEUP_SOURCE)]
    # It is no longer pending.
    assert await scheduler.pending() == []


async def test_a_fired_wakeup_never_double_fires(scheduler_db: AsyncEngine) -> None:
    queue = _RecordingQueue()
    scheduler = Scheduler(input_queue=queue, now_fn=_Clock(_BASE))
    await scheduler.schedule(delay_seconds=60, reason="once only")

    first = await scheduler.fire_due(now=_BASE + timedelta(seconds=120))
    second = await scheduler.fire_due(now=_BASE + timedelta(seconds=300))  # well past, again

    assert len(first) == 1
    assert second == []  # already fired — claimed atomically, can't repeat
    assert len(queue.pushes) == 1  # injected exactly once


async def test_fire_due_uses_injected_clock_when_now_omitted(scheduler_db: AsyncEngine) -> None:
    clock = _Clock(_BASE)
    queue = _RecordingQueue()
    scheduler = Scheduler(input_queue=queue, now_fn=clock)
    await scheduler.schedule(delay_seconds=60, reason="advance me")

    assert await scheduler.fire_due() == []  # now == _BASE, not due
    clock.now = _BASE + timedelta(seconds=120)  # advance the clock past fire_at
    fired = await scheduler.fire_due()
    assert len(fired) == 1


async def test_tool_schedules_via_the_scheduler(scheduler_db: AsyncEngine) -> None:
    scheduler = Scheduler(input_queue=_RecordingQueue(), now_fn=_Clock(_BASE))
    tool = ScheduleWakeupTool(scheduler=scheduler)

    result = await tool.run(ScheduleWakeupArgs(delay_seconds=30, reason="follow up on the article"))

    assert result.success is True
    assert result.output["id"] is not None
    assert result.output["reason"] == "follow up on the article"
    assert len(await scheduler.pending()) == 1


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"reason": "x"}, id="missing-delay"),
        pytest.param({"delay_seconds": 0, "reason": "x"}, id="zero-delay"),
        pytest.param({"delay_seconds": -5, "reason": "x"}, id="negative-delay"),
        pytest.param({"delay_seconds": 10**9, "reason": "x"}, id="delay-too-far"),
        pytest.param({"delay_seconds": 30}, id="missing-reason"),
        pytest.param({"delay_seconds": 30, "reason": ""}, id="empty-reason"),
        pytest.param({"delay_seconds": 30, "reason": "x", "extra": "no"}, id="unknown-field"),
    ],
)
def test_bad_schedule_args_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScheduleWakeupArgs.model_validate(bad)
