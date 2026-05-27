"""Inference substrate baseline: enable pgvector and create llm_call_log.

The baseline migration enables the ``vector`` extension (memory embeddings in
Phase 1 depend on it) and creates the LLM call log that backs spend tracking and
"tired" degradation.

Revision ID: 0001
Revises:
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "llm_call_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_call_log")),
    )
    op.create_index("ix_llm_call_log_ts", "llm_call_log", ["ts"])
    op.create_index("ix_llm_call_log_provider_ts", "llm_call_log", ["provider", "ts"])


def downgrade() -> None:
    op.drop_index("ix_llm_call_log_provider_ts", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_ts", table_name="llm_call_log")
    op.drop_table("llm_call_log")
    op.execute("DROP EXTENSION IF EXISTS vector")
