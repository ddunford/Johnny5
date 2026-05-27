"""The tool belt's own tables — ``note`` (journal) + ``scheduled_wakeup`` (``SPEC §9.1``).

Phase 6b gives Johnny tools that reach the world; two of them keep durable state of
their own (web reads cache into the existing ``episode``/``semantic_fact`` stores —
the autobiography *is* the web cache — and code-exec/memory-ops add no tables):

* ``note`` — his journal / knowledge base. The ``note`` tool writes a titled,
  tagged entry; reads list them newest-first. A plain log, no lifecycle.

* ``scheduled_wakeup`` — cron-like self-prompting. The ``schedule_wakeup`` tool
  persists a future ``fire_at`` + a ``reason``; the run loop's due-check (like the
  sleep trigger, FC-7) injects a self-percept when ``fire_at`` passes and flips the
  row ``pending → fired`` so a past wakeup never double-fires (TC-6b.6). The
  ``(status, fire_at)`` index serves that hot due-check query directly.

Both tables back ``safe`` tools, so every write still goes through the vetted +
audited dispatch (6a) — these tables are the tools' *state*, not a new write path.
The ORM models + repositories live with their stores (``brain/effectors/notes.py``,
``brain/effectors/scheduler.py``); this migration is the schema of record and the
index/constraint names here are mirrored there.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── note: Johnny's journal / knowledge base (append-only log, newest-first) ──
    op.create_table(
        "note",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # A short heading for the entry.
        sa.Column("title", sa.String(length=256), nullable=False),
        # The journal body — free prose, no length cap.
        sa.Column("body", sa.Text(), nullable=False),
        # Free-form tags (list of strings) for grouping/recall.
        sa.Column("tags", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_note")),
    )
    op.create_index("ix_note_ts", "note", ["ts"])

    # ── scheduled_wakeup: cron-like self-prompts the run loop's due-check fires ──
    op.create_table(
        "scheduled_wakeup",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # When the self-percept should fire (the due-check compares this to now).
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        # Why Johnny scheduled it — becomes the injected self-percept's content.
        sa.Column("reason", sa.Text(), nullable=False),
        # pending → fired. Only "pending" rows are eligible; firing flips to "fired"
        # so a past wakeup never double-fires (TC-6b.6).
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        # When the wakeup was scheduled (observability + stable ordering of ties).
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_wakeup")),
    )
    # Composite index serving the hot due-check: WHERE status='pending' AND fire_at<=now().
    op.create_index(
        "ix_scheduled_wakeup_due", "scheduled_wakeup", ["status", "fire_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_wakeup_due", table_name="scheduled_wakeup")
    op.drop_table("scheduled_wakeup")

    op.drop_index("ix_note_ts", table_name="note")
    op.drop_table("note")
