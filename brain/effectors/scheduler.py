"""``schedule_wakeup`` — a future self-prompt (``danger:safe``) + the run-loop Scheduler.

Cron-like self-prompting (``SPEC §9.1``): Johnny decides "in a while, remind me to
do X", and at that time a **self-percept** fires — a high-salience input carrying
his own earlier intention, so he "remembers" to follow up. This is what lets a goal
span more than one tick without him having to hold it in working memory.

Two pieces, mirroring how sleep is a *run-loop phase between ticks* (FC-7), not a
tick stage:

* ``ScheduleWakeupTool`` — the action a goal selects: persist a ``scheduled_wakeup``
  row (``fire_at`` = now + ``delay_seconds``, plus the ``reason``). Like every tool
  it runs only through the vetted + audited dispatch.
* ``Scheduler`` — owned by the cycle, called between ticks. ``fire_due`` atomically
  **claims** the due rows (flips ``pending → fired`` in one transaction, so a past
  wakeup can never double-fire — TC-6b.6) and then injects each as a self-percept
  onto the same ``InputQueue`` the Sensorium drains, so it surfaces on the next tick
  through the normal perception path.

The ORM model + index names mirror migration 0007. The clock is injected (``now_fn``)
so the frozen-clock cycle harness can advance time deterministically.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, DateTime, Index, String, Text, func, select, update
from sqlalchemy.orm import Mapped, mapped_column

from brain.agents.sensorium import InputQueue
from brain.effectors.tools import DangerClass, ToolResult
from brain.memory.base import utcnow
from foundation.db import Base, Repository, session_scope
from foundation.observability import get_logger

_log = get_logger("brain.effectors.scheduler")

SCHEDULED_WAKEUP_TABLE = "scheduled_wakeup"
SCHEDULE_WAKEUP_TOOL_NAME = "schedule_wakeup"

# Lifecycle: a wakeup is pending until it fires; firing flips the status so the
# due-check can never pick it up twice (TC-6b.6 "past wakeups don't double-fire").
STATUS_PENDING = "pending"
STATUS_FIRED = "fired"

# The source stamped on an injected self-percept, so it is distinguishable from a
# user message in the percept log (and could be weighted differently later).
WAKEUP_SOURCE = "scheduled_wakeup"

# Bound on how far ahead a single wakeup may be scheduled (~30 days) — Johnny can't
# park a self-prompt absurdly far out. A floor of >0 keeps "schedule in the past".
_MAX_DELAY_SECONDS = 30 * 24 * 60 * 60


# ── persistence ──────────────────────────────────────────────────────────────


class ScheduledWakeupRow(Base):
    """The ``scheduled_wakeup`` table — one row per pending/fired self-prompt."""

    __tablename__ = SCHEDULED_WAKEUP_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=STATUS_PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_scheduled_wakeup_due", "status", "fire_at"),)


class ScheduledWakeup(BaseModel):
    """A scheduled self-prompt, decoupled from the ORM/session."""

    id: int | None = None
    fire_at: datetime
    reason: str
    status: str = STATUS_PENDING
    created_at: datetime | None = None


def _row_to_wakeup(row: ScheduledWakeupRow) -> ScheduledWakeup:
    return ScheduledWakeup(
        id=row.id,
        fire_at=row.fire_at,
        reason=row.reason,
        status=row.status,
        created_at=row.created_at,
    )


class ScheduledWakeupRepository(Repository[ScheduledWakeupRow]):
    """Session-scoped persistence + the due-check query for ``scheduled_wakeup`` rows."""

    model = ScheduledWakeupRow

    async def due_pending(self, now: datetime) -> list[ScheduledWakeupRow]:
        """Pending wakeups whose ``fire_at`` has passed, oldest-due first.

        Served by ``ix_scheduled_wakeup_due`` (``status, fire_at``).
        """
        stmt = (
            select(ScheduledWakeupRow)
            .where(
                ScheduledWakeupRow.status == STATUS_PENDING,
                ScheduledWakeupRow.fire_at <= now,
            )
            .order_by(ScheduledWakeupRow.fire_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_fired(self, ids: Sequence[int]) -> None:
        """Flip the given rows ``pending → fired`` (they have now been delivered)."""
        if not ids:
            return
        await self.session.execute(
            update(ScheduledWakeupRow)
            .where(ScheduledWakeupRow.id.in_(list(ids)))
            .values(status=STATUS_FIRED)
        )

    async def pending(self, limit: int) -> list[ScheduledWakeupRow]:
        """Upcoming pending wakeups, soonest first (browse/observability)."""
        stmt = (
            select(ScheduledWakeupRow)
            .where(ScheduledWakeupRow.status == STATUS_PENDING)
            .order_by(ScheduledWakeupRow.fire_at)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


# ── the scheduler (the run-loop due-check, owned by the cycle) ─────────────────


class Scheduler:
    """Persist wakeups and fire the due ones as self-percepts (the FC-7 run-loop phase).

    Holds the same ``InputQueue`` the Sensorium drains, so a fired wakeup enters the
    normal perception path. ``now_fn`` is injected so the frozen-clock harness can
    advance past a ``fire_at`` deterministically.
    """

    def __init__(
        self,
        *,
        input_queue: InputQueue | None = None,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        self._queue = input_queue or InputQueue()
        self._now_fn = now_fn

    async def schedule(self, *, delay_seconds: float, reason: str) -> ScheduledWakeup:
        """Persist a wakeup ``delay_seconds`` from now carrying ``reason``."""
        fire_at = self._now_fn() + timedelta(seconds=delay_seconds)
        async with session_scope() as session:
            row = await ScheduledWakeupRepository(session).add(
                ScheduledWakeupRow(fire_at=fire_at, reason=reason, status=STATUS_PENDING)
            )
            return _row_to_wakeup(row)

    async def fire_due(self, *, now: datetime | None = None) -> list[ScheduledWakeup]:
        """Claim every due wakeup (atomically marked fired) and inject each self-percept.

        The claim (select due + flip ``pending → fired``) commits *before* injection,
        so a wakeup can never double-fire even if injection or the process later
        fails — at worst a single fired wakeup is missed (logged), never repeated.
        """
        moment = now if now is not None else self._now_fn()
        async with session_scope() as session:
            repo = ScheduledWakeupRepository(session)
            rows = await repo.due_pending(moment)
            if not rows:
                return []
            await repo.mark_fired([row.id for row in rows if row.id is not None])
            # Reflect the just-applied status flip on the returned objects (the bulk
            # UPDATE doesn't sync the loaded rows' in-memory ``status``).
            fired = [
                _row_to_wakeup(row).model_copy(update={"status": STATUS_FIRED}) for row in rows
            ]

        for wakeup in fired:
            await self._queue.push(wakeup.reason, source=WAKEUP_SOURCE)
            _log.info("scheduler.wakeup.fired", wakeup_id=wakeup.id, fire_at=wakeup.fire_at)
        return fired

    async def pending(self, limit: int = 50) -> list[ScheduledWakeup]:
        """Upcoming pending wakeups, soonest first."""
        async with session_scope() as session:
            rows = await ScheduledWakeupRepository(session).pending(limit)
        return [_row_to_wakeup(row) for row in rows]


# ── the tool ──────────────────────────────────────────────────────────────────


class ScheduleWakeupArgs(BaseModel):
    """Args for ``schedule_wakeup``: how long until it fires + why.

    A *relative* ``delay_seconds`` (not an absolute timestamp) keeps the tool robust
    for an LLM caller — no timezone/format parsing — and the bound stops an absurd
    far-future schedule. ``extra="forbid"`` makes a malformed action a typed error.
    """

    model_config = ConfigDict(extra="forbid")

    delay_seconds: float = Field(gt=0, le=_MAX_DELAY_SECONDS)
    reason: str = Field(min_length=1, max_length=2000)


class ScheduleWakeupTool:
    """Schedule a future self-prompt (``danger:safe`` — only Johnny's own store)."""

    name = SCHEDULE_WAKEUP_TOOL_NAME
    danger = DangerClass.SAFE
    args_schema = ScheduleWakeupArgs

    def __init__(self, *, scheduler: Scheduler | None = None) -> None:
        self._scheduler = scheduler or Scheduler()

    async def run(self, args: ScheduleWakeupArgs) -> ToolResult:
        wakeup = await self._scheduler.schedule(
            delay_seconds=args.delay_seconds, reason=args.reason
        )
        return ToolResult(
            success=True,
            output={
                "id": wakeup.id,
                "fire_at": wakeup.fire_at.isoformat(),
                "reason": wakeup.reason,
            },
            summary=f"scheduled a wakeup in {args.delay_seconds:.0f}s: {args.reason!r}",
        )
