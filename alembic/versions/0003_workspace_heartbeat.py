"""Heartbeat substrate: workspace_event, thought, percept.

The Global Workspace's durable tail. ``workspace_event`` is the append-only bus
log — every broadcast is persisted here for observability/replay (the live
stream is Redis pub/sub; this is the record). ``thought`` is the inner-monologue
stream the Narrator writes each tick. ``percept`` is the normalised input the
Sensorium produces. None of these carry embeddings — they are the cognitive
*event* log, not recallable memory (episodes in 0002 are the recallable spine).

``thought.mood_id`` is nullable and FK-less for now: the ``mood`` table lands in
Phase 3 (Affect), at which point a migration adds the constraint. Keeping the
column here means the Narrator's write path is stable across that boundary.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── workspace_event: the bus log — every broadcast, for observability ────
    op.create_table(
        "workspace_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # The publishing inner agent / subsystem (e.g. "sensorium", "narrator").
        sa.Column("module", sa.String(length=64), nullable=False),
        # The event type (e.g. "percept", "thought", "attention.selected").
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_event")),
    )
    op.create_index("ix_workspace_event_ts", "workspace_event", ["ts"])
    op.create_index("ix_workspace_event_type", "workspace_event", ["type"])

    # ── thought: the first-person inner-monologue stream ─────────────────────
    op.create_table(
        "thought",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        # Mood lands in Phase 3 (Affect); nullable + FK-less until then.
        sa.Column("mood_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_thought")),
    )
    op.create_index("ix_thought_ts", "thought", ["ts"])

    # ── percept: normalised inputs the Sensorium produces ────────────────────
    op.create_table(
        "percept",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # text | system | vision | audio | … (the sensory channel).
        sa.Column("modality", sa.String(length=32), nullable=False),
        # The raw, unprocessed input as received.
        sa.Column("raw", sa.Text(), nullable=False),
        # The structured projection the rest of the Mind consumes.
        sa.Column("normalised", postgresql.JSONB(), server_default="{}", nullable=False),
        # Where it came from (e.g. "repl", "system_metrics", "voice").
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_percept")),
    )
    op.create_index("ix_percept_ts", "percept", ["ts"])
    op.create_index("ix_percept_modality", "percept", ["modality"])


def downgrade() -> None:
    op.drop_index("ix_percept_modality", table_name="percept")
    op.drop_index("ix_percept_ts", table_name="percept")
    op.drop_table("percept")

    op.drop_index("ix_thought_ts", table_name="thought")
    op.drop_table("thought")

    op.drop_index("ix_workspace_event_type", table_name="workspace_event")
    op.drop_index("ix_workspace_event_ts", table_name="workspace_event")
    op.drop_table("workspace_event")
