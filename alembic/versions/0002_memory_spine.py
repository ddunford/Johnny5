"""Memory spine: episodic, semantic (facts + edges), and procedural stores.

Creates the four persistent memory tables (working memory lives in Redis, not
here) plus their pgvector ANN indexes. Embeddings are BGE-M3 1024-d; recall uses
cosine distance, so each ``embedding`` column gets an HNSW index with
``vector_cosine_ops`` (no training step, grows online — right for a memory that
accretes continuously). The ``vector`` extension is already enabled by 0001.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_EMBED_DIM = 1024


def upgrade() -> None:
    # ── episode: append-only autobiography ──────────────────────────────────
    op.create_table(
        "episode",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "actors",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "emotion_tags",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("salience", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("embedding", Vector(_EMBED_DIM), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_episode")),
    )
    op.create_index("ix_episode_ts", "episode", ["ts"])
    op.create_index("ix_episode_kind", "episode", ["kind"])
    # ANN index for hybrid recall's similarity component.
    op.execute(
        "CREATE INDEX ix_episode_embedding ON episode "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # ── semantic_fact: consolidated knowledge (subject/predicate unique) ─────
    op.create_table(
        "semantic_fact",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("object", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0.5", nullable=False),
        sa.Column(
            "source_episode_ids",
            postgresql.ARRAY(sa.BigInteger()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("embedding", Vector(_EMBED_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_semantic_fact")),
        # A (subject, predicate) is the upsert key: re-asserting a fact updates
        # the object/confidence rather than duplicating it.
        sa.UniqueConstraint("subject", "predicate", name=op.f("uq_semantic_fact_subject")),
    )
    op.execute(
        "CREATE INDEX ix_semantic_fact_embedding ON semantic_fact "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # ── semantic_edge: light knowledge graph over facts ─────────────────────
    op.create_table(
        "semantic_edge",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("from_fact", sa.BigInteger(), nullable=False),
        sa.Column("to_fact", sa.BigInteger(), nullable=False),
        sa.Column("relation", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_semantic_edge")),
        sa.ForeignKeyConstraint(
            ["from_fact"],
            ["semantic_fact.id"],
            name=op.f("fk_semantic_edge_from_fact_semantic_fact"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_fact"],
            ["semantic_fact.id"],
            name=op.f("fk_semantic_edge_to_fact_semantic_fact"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "from_fact",
            "to_fact",
            "relation",
            name=op.f("uq_semantic_edge_from_fact"),
        ),
    )
    op.create_index("ix_semantic_edge_from_fact", "semantic_edge", ["from_fact"])
    op.create_index("ix_semantic_edge_to_fact", "semantic_edge", ["to_fact"])

    # ── skill: procedural memory, reinforced by outcome ─────────────────────
    op.create_table(
        "skill",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("recipe", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("success_rate", sa.Float(), server_default="0", nullable=False),
        sa.Column("uses", sa.BigInteger(), server_default="0", nullable=False),
        # successes is the exact reinforcement counter; success_rate is derived
        # (successes / uses) and persisted so readers don't recompute.
        sa.Column("successes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("embedding", Vector(_EMBED_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill")),
        sa.UniqueConstraint("name", name=op.f("uq_skill_name")),
    )
    op.execute(
        "CREATE INDEX ix_skill_embedding ON skill USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_skill_embedding")
    op.drop_table("skill")

    op.drop_index("ix_semantic_edge_to_fact", table_name="semantic_edge")
    op.drop_index("ix_semantic_edge_from_fact", table_name="semantic_edge")
    op.drop_table("semantic_edge")

    op.execute("DROP INDEX IF EXISTS ix_semantic_fact_embedding")
    op.drop_table("semantic_fact")

    op.execute("DROP INDEX IF EXISTS ix_episode_embedding")
    op.drop_index("ix_episode_kind", table_name="episode")
    op.drop_index("ix_episode_ts", table_name="episode")
    op.drop_table("episode")
