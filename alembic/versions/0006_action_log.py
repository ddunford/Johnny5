"""``action_log`` — the append-only action audit trail (``SPEC §9.3``, ``§12``).

Phase 6a gives Johnny a vetted, audited path to acting. Every action the single
dispatch point handles — **allow or veto** — writes exactly one row here:

* ``tool`` / ``args`` — what was proposed (args validated against the tool schema).
* ``result`` — the tool's structured result, or ``NULL`` on a veto (it never ran).
* ``conscience_verdict`` (``allow``|``veto``) + ``veto_reason`` — the Mind's
  values-judgement on the action and, when refused, why.
* ``goal_id`` — the goal the action served (nullable: not every action has one).
* ``success`` — whether the action succeeded (always ``false`` for a veto).

This table is **append-only and the Mind cannot erase it** (FC-1): it is written
solely by the Core's ``AuditWriter`` (``core/audit.py``), import-isolated from the
Mind, and read back (newest-first) by the existing ``GET /api/v1/audit`` endpoint
+ streamed to the UI. No grant or ORM write path is exposed to ``brain`` — total
observability has to survive even a misbehaving Mind.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # The tool that ran (or would have run, on a veto).
        sa.Column("tool", sa.String(length=128), nullable=False),
        # The typed args the tool was proposed with.
        sa.Column("args", postgresql.JSONB(), server_default="{}", nullable=False),
        # The tool's result; NULL when vetoed (the tool never ran).
        sa.Column("result", postgresql.JSONB(), nullable=True),
        # "allow" | "veto" — the Conscience's values-judgement on this action.
        sa.Column("conscience_verdict", sa.String(length=16), nullable=False),
        # Why the Conscience vetoed (NULL when allowed).
        sa.Column("veto_reason", sa.Text(), nullable=True),
        # The goal this action served (NULL for an action with no owning goal).
        sa.Column("goal_id", sa.BigInteger(), nullable=True),
        # Whether the action succeeded (always false for a veto).
        sa.Column("success", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_log")),
    )
    op.create_index("ix_action_log_ts", "action_log", ["ts"])
    op.create_index("ix_action_log_tool", "action_log", ["tool"])


def downgrade() -> None:
    op.drop_index("ix_action_log_tool", table_name="action_log")
    op.drop_index("ix_action_log_ts", table_name="action_log")
    op.drop_table("action_log")
