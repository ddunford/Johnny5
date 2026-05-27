"""Goals — what an urge becomes once promoted, and how they persist (``SPEC §6.3``).

A ``Goal`` has a source (the drive that spawned it, or ``user``), a description,
a priority (drive intensity × affect at promotion), a status, the plan
Deliberation chose, and an outcome that feeds back into drives/affect/memory. The
``goal`` table persists across restarts — an *active* goal is reloaded on boot, so
Johnny resumes what he was pursuing rather than starting blank (TC-3.5). Because
the hysteresis window keys off ``created_at`` (persisted, not in-memory arbiter
state), the anti-thrash behaviour survives a restart too.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, DateTime, Float, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from brain.memory.base import utcnow
from foundation.db import Base, Repository, session_scope

GOAL_TABLE = "goal"

# Lifecycle states.
STATUS_ACTIVE = "active"
STATUS_RESOLVED = "resolved"
STATUS_ABANDONED = "abandoned"

# The source for a goal Johnny set himself (vs a drive name or "user").
SOURCE_USER = "user"


# ── persistence ──────────────────────────────────────────────────────────────


class GoalRow(Base):
    """The ``goal`` table — one row per goal, persisted across restarts."""

    __tablename__ = GOAL_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=STATUS_ACTIVE)
    plan: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    outcome: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Goal(BaseModel):
    """A goal, decoupled from the ORM/session."""

    id: int | None = None
    source: str
    description: str
    priority: float = 0.0
    status: str = STATUS_ACTIVE
    plan: dict[str, object] = Field(default_factory=dict)
    outcome: dict[str, object] = Field(default_factory=dict)
    created_at: datetime | None = None
    resolved_at: datetime | None = None


def _row_to_goal(row: GoalRow) -> Goal:
    return Goal(
        id=row.id,
        source=row.source,
        description=row.description,
        priority=row.priority,
        status=row.status,
        plan=dict(row.plan),
        outcome=dict(row.outcome),
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


class GoalRepository(Repository[GoalRow]):
    """Session-scoped persistence + lifecycle queries for ``goal`` rows."""

    model = GoalRow

    async def active(self) -> list[GoalRow]:
        """Active goals, most-recently-promoted first (the incumbent is first)."""
        stmt = (
            select(GoalRow)
            .where(GoalRow.status == STATUS_ACTIVE)
            .order_by(GoalRow.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def recent_closed(self, limit: int) -> list[GoalRow]:
        """Recently resolved/abandoned goals, newest first (the goal-history view)."""
        stmt = (
            select(GoalRow)
            .where(GoalRow.status != STATUS_ACTIVE)
            .order_by(GoalRow.resolved_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


# ── the store (the persistence boundary the arbiter + deliberation use) ────────


class GoalStore:
    """Persist goals and drive their lifecycle (promote → resolve/abandon).

    ``now`` comes from an injected clock so resolution/abandon timestamps are
    freezable in tests (the same seam as the rest of Phase 3).
    """

    def __init__(self, *, now_fn: Callable[[], datetime] = utcnow) -> None:
        self._now_fn = now_fn

    async def active(self) -> list[Goal]:
        """The current active goals (incumbent first)."""
        async with session_scope() as session:
            rows = await GoalRepository(session).active()
        return [_row_to_goal(row) for row in rows]

    async def recent_closed(self, limit: int = 20) -> list[Goal]:
        """Recently resolved/abandoned goals, newest first (goal history for the UI)."""
        async with session_scope() as session:
            rows = await GoalRepository(session).recent_closed(limit)
        return [_row_to_goal(row) for row in rows]

    async def promote(self, goal: Goal) -> Goal:
        """Persist a new active goal and return it with id + created_at."""
        async with session_scope() as session:
            row = await GoalRepository(session).add(
                GoalRow(
                    source=goal.source,
                    description=goal.description,
                    priority=goal.priority,
                    status=STATUS_ACTIVE,
                    plan=dict(goal.plan),
                    created_at=goal.created_at or self._now_fn(),
                )
            )
            return _row_to_goal(row)

    async def set_plan(self, goal_id: int, plan: dict[str, object]) -> None:
        """Record the plan Deliberation chose for a goal."""
        async with session_scope() as session:
            row = await GoalRepository(session).get(goal_id)
            if row is not None:
                row.plan = dict(plan)

    async def resolve(self, goal_id: int, outcome: dict[str, object]) -> None:
        """Mark a goal resolved with its outcome (feeds drives/affect/memory)."""
        await self._close(goal_id, STATUS_RESOLVED, outcome)

    async def abandon(self, goal_id: int, outcome: dict[str, object] | None = None) -> None:
        """Mark a goal abandoned (a stronger drive displaced it after the dwell)."""
        await self._close(goal_id, STATUS_ABANDONED, outcome or {})

    async def _close(self, goal_id: int, status: str, outcome: dict[str, object]) -> None:
        async with session_scope() as session:
            row = await GoalRepository(session).get(goal_id)
            if row is None:
                return
            row.status = status
            row.outcome = dict(outcome)
            row.resolved_at = self._now_fn()


def goals_to_payload(goals: Sequence[Goal]) -> list[dict[str, object]]:
    """A compact serialisation of goals for the state surface (``/ws/state``)."""
    return [
        {
            "id": g.id,
            "source": g.source,
            "description": g.description,
            "priority": round(g.priority, 4),
            "status": g.status,
            "plan": g.plan,
        }
        for g in goals
    ]
