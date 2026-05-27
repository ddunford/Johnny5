"""Episodic memory — Johnny's timestamped autobiography.

``EpisodeRow`` is the append-only ``episode`` table (never hard-deleted in v1).
``Episode`` is the session-independent domain object the rest of the Mind passes
in and gets back. The write path embeds content via the Phase 0 ``Embedder``;
recall is *hybrid* — it blends vector similarity with recency and salience
(Generative Agents pattern) so recall feels mind-like rather than just topically
near. See ``EpisodicMemory`` (added with the write/recall path).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, DateTime, Float, String, Text, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from brain.memory import EMBED_DIM
from brain.memory.base import (
    EmbeddingClient,
    RecallWeights,
    blended_score,
    clamp01,
    recency_score,
    similarity_from_distance,
)
from foundation.config import get_settings
from foundation.db import Base, Repository, session_scope

EPISODE_TABLE = "episode"


class EpisodeRow(Base):
    """The ``episode`` table — one row per remembered event."""

    __tablename__ = EPISODE_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    actors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    emotion_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    salience: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM), nullable=False)


class Episode(BaseModel):
    """A remembered event, decoupled from the ORM/session.

    ``ts`` defaults to write time when omitted. ``score`` is populated only on
    the objects returned by ``EpisodicMemory.recall`` (the blended relevance
    used to rank them); it is ``None`` for freshly written/fetched episodes.
    """

    id: int | None = None
    ts: datetime | None = None
    kind: str
    content: str
    actors: list[str] = Field(default_factory=list)
    emotion_tags: list[str] = Field(default_factory=list)
    salience: float = 0.5
    score: float | None = None


def _row_to_episode(row: EpisodeRow, *, score: float | None = None) -> Episode:
    return Episode(
        id=row.id,
        ts=row.ts,
        kind=row.kind,
        content=row.content,
        actors=list(row.actors),
        emotion_tags=list(row.emotion_tags),
        salience=row.salience,
        score=score,
    )


class EpisodeRepository(Repository[EpisodeRow]):
    """Session-scoped persistence + vector search for ``episode`` rows."""

    model = EpisodeRow

    async def nearest(
        self, embedding: Sequence[float], limit: int
    ) -> list[tuple[EpisodeRow, float]]:
        """Return ``(row, cosine_distance)`` for the ``limit`` nearest episodes.

        The HNSW index serves this; the smaller the distance the closer the
        match. This is the similarity gate — recency/salience re-rank the result.
        """
        distance = EpisodeRow.embedding.cosine_distance(embedding).label("distance")
        stmt = select(EpisodeRow, distance).order_by(distance).limit(limit)
        result = await self.session.execute(stmt)
        return [(row, float(dist)) for row, dist in result.all()]

    async def recent(self, limit: int) -> list[EpisodeRow]:
        """Most recent episodes first (feeds the consolidator)."""
        stmt = select(EpisodeRow).order_by(EpisodeRow.ts.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class EpisodicMemory:
    """Write + hybrid-recall over episodic memory.

    Embeds exclusively through the injected ``EmbeddingClient`` (FC-4: never
    inline). Recall blends similarity, recency, and salience — pure cosine would
    surface topically-near but stale/trivial memories, which is the failure mode
    ``SPEC §8`` calls out.
    """

    def __init__(
        self,
        embedder: EmbeddingClient,
        *,
        weights: RecallWeights | None = None,
        candidate_pool: int | None = None,
    ) -> None:
        settings = get_settings()
        self._embedder = embedder
        self._weights = weights or RecallWeights.from_settings(settings)
        self._candidate_pool = candidate_pool or settings.memory_recall_candidate_pool

    async def write(self, episode: Episode) -> Episode:
        """Persist an episode (embedding its content) and return it with id + ts."""
        vector = await self._embedder.embed_one(episode.content)
        ts = episode.ts or datetime.now(UTC)
        async with session_scope() as session:
            repo = EpisodeRepository(session)
            row = await repo.add(
                EpisodeRow(
                    ts=ts,
                    kind=episode.kind,
                    content=episode.content,
                    actors=list(episode.actors),
                    emotion_tags=list(episode.emotion_tags),
                    salience=clamp01(episode.salience),
                    embedding=vector,
                )
            )
            return _row_to_episode(row)

    async def recall(
        self, query: str, k: int = 5, *, now: datetime | None = None
    ) -> list[Episode]:
        """Recall the ``k`` most relevant episodes for ``query`` by the hybrid blend.

        Pulls a similarity-ordered candidate pool from the ANN index, then
        re-ranks each candidate by ``w_sim·sim + w_rec·recency + w_sal·salience``
        and returns the top ``k`` with their blended ``score`` populated.
        """
        if k <= 0:
            return []
        reference = now or datetime.now(UTC)
        query_vector = await self._embedder.embed_one(query)
        pool = max(self._candidate_pool, k)
        async with session_scope() as session:
            candidates = await EpisodeRepository(session).nearest(query_vector, pool)

        scored: list[Episode] = []
        for row, distance in candidates:
            score = blended_score(
                similarity=similarity_from_distance(distance),
                recency=recency_score(row.ts, reference, self._weights.recency_halflife_seconds),
                salience=clamp01(row.salience),
                weights=self._weights,
            )
            scored.append(_row_to_episode(row, score=score))

        scored.sort(key=lambda episode: episode.score or 0.0, reverse=True)
        return scored[:k]
