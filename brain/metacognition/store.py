"""Self-improvement note persistence — Metacognition's review output (``SPEC §12``).

``SelfImprovementNoteRow`` is the ``self_improvement_note`` table: an observation
about how Johnny is functioning plus a proposed change, with provenance. ``status``
is **informational only** this phase (always ``open``) — *applying* a proposal is
the Phase-9 gated self-edit flow, never here. The store is append-only (a log of
reviews); ``recent`` feeds the REPL's "latest reflection" view.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, DateTime, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from brain.memory.base import utcnow
from foundation.db import Base, Repository, session_scope

SELF_IMPROVEMENT_NOTE_TABLE = "self_improvement_note"

# The only status this phase — a proposal is recorded, never acted on (Phase 9 owns
# accept/apply/reject). Kept as a constant so the "proposes only" invariant is explicit.
STATUS_OPEN = "open"


# ── persistence ──────────────────────────────────────────────────────────────


class SelfImprovementNoteRow(Base):
    """The ``self_improvement_note`` table — one row per proposal (append-only log)."""

    __tablename__ = SELF_IMPROVEMENT_NOTE_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    proposal: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=STATUS_OPEN)


class SelfImprovementNote(BaseModel):
    """A self-improvement note, decoupled from the ORM/session."""

    id: int | None = None
    ts: datetime | None = None
    observation: str
    proposal: str
    source: dict[str, object] = Field(default_factory=dict)
    status: str = STATUS_OPEN


def _row_to_note(row: SelfImprovementNoteRow) -> SelfImprovementNote:
    return SelfImprovementNote(
        id=row.id,
        ts=row.ts,
        observation=row.observation,
        proposal=row.proposal,
        source=dict(row.source),
        status=row.status,
    )


class SelfImprovementNoteRepository(Repository[SelfImprovementNoteRow]):
    """Session-scoped persistence + recent-query for ``self_improvement_note`` rows."""

    model = SelfImprovementNoteRow

    async def recent(self, limit: int) -> list[SelfImprovementNoteRow]:
        """The most recent notes first (feeds the REPL's latest-reflection view)."""
        result = await self.session.execute(
            select(SelfImprovementNoteRow).order_by(SelfImprovementNoteRow.ts.desc()).limit(limit)
        )
        return list(result.scalars().all())


# ── the store ───────────────────────────────────────────────────────────────


class MetacognitionStore:
    """Append self-improvement notes and read the recent ones.

    ``now`` is injected so the note timestamp is freezable in tests.
    """

    def __init__(self, *, now_fn: Callable[[], datetime] = utcnow) -> None:
        self._now_fn = now_fn

    async def add_note(self, note: SelfImprovementNote) -> SelfImprovementNote:
        """Persist one note (always ``status=open`` — proposals are never applied here)."""
        async with session_scope() as session:
            row = await SelfImprovementNoteRepository(session).add(
                SelfImprovementNoteRow(
                    ts=note.ts or self._now_fn(),
                    observation=note.observation,
                    proposal=note.proposal,
                    source=dict(note.source),
                    status=STATUS_OPEN,
                )
            )
            return _row_to_note(row)

    async def recent(self, limit: int = 10) -> list[SelfImprovementNote]:
        """The most recent notes (newest first)."""
        async with session_scope() as session:
            rows = await SelfImprovementNoteRepository(session).recent(limit)
        return [_row_to_note(row) for row in rows]
