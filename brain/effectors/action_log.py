"""The ``action_log`` — the append-only audit trail of every dispatched action.

One row per action the dispatch point handled, **allow or veto** (``SPEC §9.3``,
``§12``): the tool, its args, the result (null on a veto — the tool never ran),
the Conscience verdict + veto reason, the goal it served, and whether it
succeeded. This is the durable, total-observability record that survives even a
misbehaving Mind.

**Append-only, and the Mind can't erase it (FC-1).** Writes go through the Core's
``AuditWriter`` (``core/audit.py``), which is import-isolated from ``brain``. This
module is the *read* side only — it is deliberately read-only: there is no
``add``/``update``/``delete`` path here, so nothing in the Mind can rewrite or
truncate its own audit trail. The single physical table name lives here and is
referenced *by name* (never imported) by the Core writer, mirroring how
``core/governors.py`` reads ``llm_call_log``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from foundation.db import Base

# The physical table name. The Core's AuditWriter writes to this name without
# importing this module (FC-1); keep the two in sync if it ever changes.
ACTION_LOG_TABLE = "action_log"

# The two verdict values the Conscience can return (mirrored in the Conscience
# agent — kept as bare constants here so the read model has no Mind dependency).
VERDICT_ALLOW = "allow"
VERDICT_VETO = "veto"


class ActionLogRow(Base):
    """The ``action_log`` table — the append-only action audit trail.

    Defined as an ORM model so Alembic sees the schema and the read side is
    typed, but **no write method is exposed from the Mind** (see module docstring):
    ``ActionLogRepository`` only reads. The Core writer inserts rows by raw SQL.
    """

    __tablename__ = ACTION_LOG_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # The tool that ran (or would have run, on a veto).
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    # The typed args the tool was proposed with (validated against its schema).
    args: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # The tool's result, or NULL when the action was vetoed (it never ran).
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    # "allow" | "veto" — the Conscience's call on this action.
    conscience_verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    # Why the Conscience vetoed (NULL when allowed).
    veto_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The goal this action served (NULL for an action with no owning goal).
    goal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Whether the action succeeded (always False for a veto — it never ran).
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        Index("ix_action_log_ts", "ts"),
        Index("ix_action_log_tool", "tool"),
    )


class ActionLogRepository:
    """Read-only access to ``action_log`` (FC-1: the Mind cannot mutate its trail).

    Deliberately does **not** subclass the generic ``Repository`` — it exposes no
    ``add``/``update``/``delete``. Writes are the Core's job (``core/audit.py``);
    this is the projection the ``/api/v1/audit`` endpoint and the UI read from.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def recent(self, limit: int, *, verdict_filter: str | None = None) -> list[ActionLogRow]:
        """Most-recent actions first; ``verdict_filter`` keeps only allow/veto rows."""
        stmt = select(ActionLogRow).order_by(ActionLogRow.ts.desc()).limit(limit)
        if verdict_filter is not None:
            stmt = stmt.where(ActionLogRow.conscience_verdict == verdict_filter)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
