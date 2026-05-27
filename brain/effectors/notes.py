"""Johnny's journal — the ``note`` table + the ``note`` tool (``danger:safe``).

A note is a titled, tagged journal/knowledge-base entry Johnny writes for himself:
a place to record a thought, a finding from a web read, a plan. The ``note`` tool
is the *action* (write); reading is a plain query (``NoteStore.recent``) that the
``/api/v1`` read surface (TASK-6b.10) exposes to the UI. Like every tool it runs
only through the vetted + audited dispatch (6a), so a journal write is
Conscience-vetted and lands in the ``action_log`` — the table is the tool's state,
not a new write path.

Mirrors the ``MetacognitionStore`` shape: an ORM row, a session-independent
domain model, a thin repository, and a store with an injected clock (so the
timestamp is freezable in tests). The index/constraint names match migration 0007.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, DateTime, Index, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from brain.effectors.tools import DangerClass, ToolResult
from brain.memory.base import utcnow
from foundation.db import Base, Repository, session_scope

NOTE_TABLE = "note"
NOTE_TOOL_NAME = "note"


# ── persistence ──────────────────────────────────────────────────────────────


class NoteRow(Base):
    """The ``note`` table — one row per journal entry (append-only log)."""

    __tablename__ = NOTE_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")

    __table_args__ = (Index("ix_note_ts", "ts"),)


class Note(BaseModel):
    """A journal note, decoupled from the ORM/session."""

    id: int | None = None
    ts: datetime | None = None
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)


def _row_to_note(row: NoteRow) -> Note:
    return Note(id=row.id, ts=row.ts, title=row.title, body=row.body, tags=list(row.tags))


class NoteRepository(Repository[NoteRow]):
    """Session-scoped persistence + recent-query for ``note`` rows."""

    model = NoteRow

    async def recent(self, limit: int) -> list[NoteRow]:
        """The most recent notes first (the journal read surface)."""
        result = await self.session.execute(
            select(NoteRow).order_by(NoteRow.ts.desc()).limit(limit)
        )
        return list(result.scalars().all())


class NoteStore:
    """Write journal notes and read the recent ones (the persistence boundary).

    ``now`` is injected so the note timestamp is freezable in tests (the same seam
    as the goal/metacognition stores).
    """

    def __init__(self, *, now_fn: Callable[[], datetime] = utcnow) -> None:
        self._now_fn = now_fn

    async def add(self, note: Note) -> Note:
        """Persist one note and return it with id + ts."""
        async with session_scope() as session:
            row = await NoteRepository(session).add(
                NoteRow(
                    ts=note.ts or self._now_fn(),
                    title=note.title,
                    body=note.body,
                    tags=list(note.tags),
                )
            )
            return _row_to_note(row)

    async def recent(self, limit: int = 20) -> list[Note]:
        """The most recent notes, newest first (the journal read for the UI)."""
        if limit <= 0:
            return []
        async with session_scope() as session:
            rows = await NoteRepository(session).recent(limit)
        return [_row_to_note(row) for row in rows]


def notes_to_payload(notes: Sequence[Note]) -> list[dict[str, object]]:
    """A compact serialisation of notes for the ``/api/v1`` read surface."""
    return [
        {
            "id": n.id,
            "ts": n.ts.isoformat() if n.ts else None,
            "title": n.title,
            "body": n.body,
            "tags": n.tags,
        }
        for n in notes
    ]


# ── the tool ──────────────────────────────────────────────────────────────────


class NoteArgs(BaseModel):
    """Args for the ``note`` tool: a ``title`` + ``body`` and optional ``tags``.

    ``extra="forbid"`` makes the schema strict so a malformed action raises a typed
    ``ValidationError`` at the dispatch's vet step (before anything is written).
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class NoteTool:
    """Write a journal note (``danger:safe`` — it touches only Johnny's own store)."""

    name = NOTE_TOOL_NAME
    danger = DangerClass.SAFE
    args_schema = NoteArgs

    def __init__(self, *, store: NoteStore | None = None) -> None:
        self._store = store or NoteStore()

    async def run(self, args: NoteArgs) -> ToolResult:
        note = await self._store.add(Note(title=args.title, body=args.body, tags=args.tags))
        return ToolResult(
            success=True,
            output={"id": note.id, "title": note.title, "tags": note.tags},
            summary=f"journalled {note.title!r}",
        )
