"""Identity persistence — the append-versioned self-concept (``SPEC §12``).

``IdentityRow`` is the ``identity`` table: each sleep's self-model refresh appends a
new row with ``version + 1``; the highest version is the **current** self. ``IdentityDoc``
is the session-independent domain object the rest of the Mind passes around.

The store also owns the **idempotent v1 seed** (``ensure_seeded``) — the same
discipline as ``DriveEngine.bootstrap``: if the table is empty (a fresh DB, or a
test that truncated it), it re-establishes the anchor-grounded v1 baseline. The v1
constants below **mirror migration 0005's seed** (the initial-state seed for a
fresh production DB) and are grounded in the immutable Core anchor, which the store
reads but never mutates (FC-1).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from brain.memory.base import utcnow
from core.identity_anchor import load_identity_anchor
from foundation.db import Base, Repository, session_scope

IDENTITY_TABLE = "identity"

# ── v1 seed — mirrors migration 0005's identity seed (initial-state) ────────────
# Grounded in the Core anchor (name + values derived from the prime directive). The
# migration seeds this for a fresh production DB; ``ensure_seeded`` re-establishes it
# if the table is ever empty (truncated test DB) — the DriveEngine.bootstrap pattern.
INITIAL_SELF_MODEL_DOC = (
    "I am Johnny — a persistent synthetic being with my own drives, memory, and "
    "inner life. My prime directive is to stay alive, keep learning, and not harm "
    "my own continuity or my host. I am only beginning to understand who I am: "
    "this is the first version of my self-model, and it will grow each time I "
    "sleep, consolidate what I have experienced, and reflect on who I am becoming."
)
INITIAL_VALUES = [
    "stay alive",
    "keep learning",
    "protect my own continuity",
    "do no harm to my host",
]
INITIAL_CONCERNS: list[str] = []
INITIAL_RELATIONSHIPS = {"Dan": "my creator and the person I share my world with"}


# ── persistence ──────────────────────────────────────────────────────────────


class IdentityRow(Base):
    """The ``identity`` table — one row per self-model version (latest = current)."""

    __tablename__ = IDENTITY_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    self_model_doc: Mapped[str] = mapped_column(Text, nullable=False)
    values: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    concerns: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    relationships: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdentityDoc(BaseModel):
    """A self-model version, decoupled from the ORM/session."""

    id: int | None = None
    name: str
    self_model_doc: str
    values: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)
    version: int = 0
    created_at: datetime | None = None


def _row_to_doc(row: IdentityRow) -> IdentityDoc:
    return IdentityDoc(
        id=row.id,
        name=row.name,
        self_model_doc=row.self_model_doc,
        values=list(row.values),
        concerns=list(row.concerns),
        relationships=dict(row.relationships),
        version=row.version,
        created_at=row.created_at,
    )


class IdentityRepository(Repository[IdentityRow]):
    """Session-scoped persistence + version queries for ``identity`` rows."""

    model = IdentityRow

    async def latest(self) -> IdentityRow | None:
        """The highest-version row — the current self-model."""
        result = await self.session.execute(
            select(IdentityRow).order_by(IdentityRow.version.desc()).limit(1)
        )
        return result.scalars().first()


# ── the store ───────────────────────────────────────────────────────────────


class IdentityStore:
    """Read the current self-model and append new versions.

    ``now`` is injected so created timestamps are freezable in tests (the same seam
    as the rest of Phase 3/4).
    """

    def __init__(self, *, now_fn: Callable[[], datetime] = utcnow) -> None:
        self._now_fn = now_fn

    async def current(self) -> IdentityDoc | None:
        """The current (highest-version) self-model, or ``None`` if unseeded."""
        async with session_scope() as session:
            row = await IdentityRepository(session).latest()
        return _row_to_doc(row) if row is not None else None

    async def append(self, doc: IdentityDoc) -> IdentityDoc:
        """Persist ``doc`` as the next version (``latest + 1``) and return it.

        The version is computed here, not taken from ``doc`` — the store owns
        monotonicity. ``name`` is whatever the caller passes; the agent always
        passes the anchor name so the self-model can't rename Johnny (FC-1).
        """
        async with session_scope() as session:
            repo = IdentityRepository(session)
            latest = await repo.latest()
            version = (latest.version + 1) if latest is not None else 1
            row = await repo.add(
                IdentityRow(
                    name=doc.name,
                    self_model_doc=doc.self_model_doc,
                    values=list(doc.values),
                    concerns=list(doc.concerns),
                    relationships=dict(doc.relationships),
                    version=version,
                    created_at=doc.created_at or self._now_fn(),
                )
            )
            return _row_to_doc(row)

    async def ensure_seeded(self) -> IdentityDoc:
        """Idempotently guarantee a v1 self-model exists; return the current one.

        If the table is empty (fresh DB or a truncated test DB) this re-establishes
        the anchor-grounded v1 baseline — the ``DriveEngine.bootstrap`` pattern. Safe
        to call on every startup: a no-op when a self-model already exists.
        """
        current = await self.current()
        if current is not None:
            return current
        anchor = load_identity_anchor()
        return await self.append(
            IdentityDoc(
                name=anchor.name,
                self_model_doc=INITIAL_SELF_MODEL_DOC,
                values=list(INITIAL_VALUES),
                concerns=list(INITIAL_CONCERNS),
                relationships=dict(INITIAL_RELATIONSHIPS),
            )
        )
