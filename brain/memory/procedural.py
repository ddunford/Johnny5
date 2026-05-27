"""Procedural memory — skills and tool recipes, reinforced by outcome.

``SkillRow`` is the ``skill`` table; ``name`` is unique so storing a same-named
skill updates its recipe rather than duplicating. ``success_rate`` is derived
(``successes / uses``) and persisted so readers don't recompute; ``successes`` is
the exact reinforcement counter. ``Skill`` is the session-independent domain
object. Recall is by vector similarity over the skill's description. See
``ProceduralMemory``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, DateTime, Float, String, cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from brain.memory import EMBED_DIM
from brain.memory.base import EmbeddingClient, clamp01, similarity_from_distance
from foundation.db import Base, Repository, session_scope

SKILL_TABLE = "skill"


class SkillRow(Base):
    """The ``skill`` table — one row per learned procedure."""

    __tablename__ = SKILL_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    recipe: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    success_rate: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    uses: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    successes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Skill(BaseModel):
    """A learned procedure, decoupled from the ORM/session.

    ``description`` is the text the store embeds for similarity ``find``; it is
    not persisted as a column (the recipe + name carry the durable content), but
    callers supply it so the skill is findable by intent.
    """

    id: int | None = None
    name: str
    description: str = ""
    recipe: dict[str, Any] = Field(default_factory=dict)
    success_rate: float = 0.0
    uses: int = 0
    successes: int = 0
    score: float | None = None


def _skill_text(skill: Skill) -> str:
    """The text a skill is embedded as, so ``find`` matches it by intent."""
    return f"{skill.name} {skill.description}".strip()


def _row_to_skill(row: SkillRow, *, score: float | None = None) -> Skill:
    return Skill(
        id=row.id,
        name=row.name,
        recipe=dict(row.recipe),
        success_rate=row.success_rate,
        uses=row.uses,
        successes=row.successes,
        score=score,
    )


class SkillRepository(Repository[SkillRow]):
    """Session-scoped persistence + vector search for ``skill`` rows."""

    model = SkillRow

    async def nearest(
        self, embedding: Sequence[float], limit: int
    ) -> list[tuple[SkillRow, float]]:
        distance = SkillRow.embedding.cosine_distance(embedding).label("distance")
        stmt = select(SkillRow, distance).order_by(distance).limit(limit)
        result = await self.session.execute(stmt)
        return [(row, float(dist)) for row, dist in result.all()]


class ProceduralMemory:
    """Store, find-by-intent, and outcome-reinforce skills.

    ``success_rate`` is kept exact as ``successes / uses`` and persisted.
    Re-storing a known skill (by name) refreshes its recipe/embedding but
    preserves its reinforcement history — relearning the recipe shouldn't wipe
    how well it has worked. Embedding is always via the injected client (FC-4).
    """

    def __init__(self, embedder: EmbeddingClient) -> None:
        self._embedder = embedder

    async def store(self, skill: Skill) -> Skill:
        """Insert a skill, or update its recipe/embedding if the name exists."""
        embedding = await self._embedder.embed_one(_skill_text(skill))
        uses = max(0, skill.uses)
        successes = min(max(0, skill.successes), uses)
        insert = pg_insert(SkillRow).values(
            name=skill.name,
            recipe=skill.recipe,
            success_rate=(successes / uses) if uses else clamp01(skill.success_rate),
            uses=uses,
            successes=successes,
            embedding=embedding,
        )
        stmt = insert.on_conflict_do_update(
            index_elements=["name"],
            set_={
                "recipe": insert.excluded.recipe,
                "embedding": insert.excluded.embedding,
                "updated_at": func.now(),
            },
        ).returning(SkillRow)
        async with session_scope() as session:
            result = await session.execute(stmt)
            return _row_to_skill(result.scalar_one())

    async def find(self, query: str, k: int = 5) -> list[Skill]:
        """Find the ``k`` skills most similar to ``query`` (cosine), ``score`` set."""
        if k <= 0:
            return []
        query_vector = await self._embedder.embed_one(query)
        async with session_scope() as session:
            candidates = await SkillRepository(session).nearest(query_vector, k)
        return [
            _row_to_skill(row, score=similarity_from_distance(distance))
            for row, distance in candidates
        ]

    async def reinforce(self, skill_id: int, success: bool) -> Skill:
        """Record one outcome: bump ``uses`` (and ``successes`` on success), recompute rate."""
        new_uses = SkillRow.uses + 1
        new_successes = SkillRow.successes + (1 if success else 0)
        stmt = (
            update(SkillRow)
            .where(SkillRow.id == skill_id)
            .values(
                uses=new_uses,
                successes=new_successes,
                # cast avoids Postgres integer division.
                success_rate=cast(new_successes, Float) / cast(new_uses, Float),
                updated_at=func.now(),
            )
            .returning(SkillRow)
        )
        async with session_scope() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise ValueError(f"skill {skill_id} not found")
            return _row_to_skill(row)
