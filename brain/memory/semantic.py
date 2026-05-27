"""Semantic memory — consolidated facts plus a light knowledge graph.

``SemanticFactRow`` is the ``semantic_fact`` table; ``(subject, predicate)`` is
unique so re-asserting a fact updates it rather than duplicating. ``SemanticEdgeRow``
is the ``semantic_edge`` table — a thin relation between two facts. ``SemanticFact``
and ``SemanticEdge`` are the session-independent domain objects. Recall is by
vector similarity (facts are consolidated knowledge, not time-stamped events, so
they don't carry the episodic recency weighting). See ``SemanticMemory``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, String, Text, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from brain.memory import EMBED_DIM
from brain.memory.base import EmbeddingClient, clamp01, similarity_from_distance
from foundation.db import Base, Repository, session_scope

SEMANTIC_FACT_TABLE = "semantic_fact"
SEMANTIC_EDGE_TABLE = "semantic_edge"


class SemanticFactRow(Base):
    """The ``semantic_fact`` table — a subject/predicate/object triple."""

    __tablename__ = SEMANTIC_FACT_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    source_episode_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default="{}"
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SemanticEdgeRow(Base):
    """The ``semantic_edge`` table — a directed, labelled link between facts."""

    __tablename__ = SEMANTIC_EDGE_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    from_fact: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("semantic_fact.id", ondelete="CASCADE"), nullable=False
    )
    to_fact: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("semantic_fact.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(128), nullable=False)


class SemanticFact(BaseModel):
    """A consolidated fact, decoupled from the ORM/session."""

    id: int | None = None
    subject: str
    predicate: str
    object: str
    confidence: float = 0.5
    source_episode_ids: list[int] = Field(default_factory=list)
    score: float | None = None


class SemanticEdge(BaseModel):
    """A relation between two facts."""

    id: int | None = None
    from_fact: int
    to_fact: int
    relation: str


def _fact_text(subject: str, predicate: str, obj: str) -> str:
    """The text a fact is embedded as (so recall matches its meaning)."""
    return f"{subject} {predicate} {obj}"


def _row_to_fact(row: SemanticFactRow, *, score: float | None = None) -> SemanticFact:
    return SemanticFact(
        id=row.id,
        subject=row.subject,
        predicate=row.predicate,
        object=row.object,
        confidence=row.confidence,
        source_episode_ids=list(row.source_episode_ids),
        score=score,
    )


def _row_to_edge(row: SemanticEdgeRow) -> SemanticEdge:
    return SemanticEdge(
        id=row.id, from_fact=row.from_fact, to_fact=row.to_fact, relation=row.relation
    )


class SemanticFactRepository(Repository[SemanticFactRow]):
    """Session-scoped persistence + vector search for ``semantic_fact`` rows."""

    model = SemanticFactRow

    async def nearest(
        self, embedding: Sequence[float], limit: int
    ) -> list[tuple[SemanticFactRow, float]]:
        distance = SemanticFactRow.embedding.cosine_distance(embedding).label("distance")
        stmt = select(SemanticFactRow, distance).order_by(distance).limit(limit)
        result = await self.session.execute(stmt)
        return [(row, float(dist)) for row, dist in result.all()]


class SemanticEdgeRepository(Repository[SemanticEdgeRow]):
    """Session-scoped persistence + traversal for ``semantic_edge`` rows."""

    model = SemanticEdgeRow

    async def neighbours(
        self, fact_id: int, *, relation: str | None = None
    ) -> list[SemanticFactRow]:
        """Facts reachable from ``fact_id`` via an outgoing edge."""
        stmt = (
            select(SemanticFactRow)
            .join(SemanticEdgeRow, SemanticEdgeRow.to_fact == SemanticFactRow.id)
            .where(SemanticEdgeRow.from_fact == fact_id)
        )
        if relation is not None:
            stmt = stmt.where(SemanticEdgeRow.relation == relation)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class SemanticMemory:
    """Fact upsert + similarity recall + a light knowledge graph over facts.

    Facts are consolidated knowledge, not timestamped events, so recall is pure
    vector similarity (no recency weighting — that belongs to episodic memory).
    Embedding is always via the injected ``EmbeddingClient`` (FC-4).
    """

    def __init__(self, embedder: EmbeddingClient) -> None:
        self._embedder = embedder

    async def upsert_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        confidence: float = 0.5,
        source_episode_ids: Sequence[int] | None = None,
    ) -> SemanticFact:
        """Insert a fact, or update it in place if ``(subject, predicate)`` exists.

        Re-asserting a fact overwrites its object/confidence/provenance/embedding
        and bumps ``updated_at`` — never a duplicate row.
        """
        sources = list(source_episode_ids or [])
        embedding = await self._embedder.embed_one(_fact_text(subject, predicate, obj))
        insert = pg_insert(SemanticFactRow).values(
            subject=subject,
            predicate=predicate,
            object=obj,
            confidence=clamp01(confidence),
            source_episode_ids=sources,
            embedding=embedding,
        )
        stmt = insert.on_conflict_do_update(
            index_elements=["subject", "predicate"],
            set_={
                "object": insert.excluded.object,
                "confidence": insert.excluded.confidence,
                "source_episode_ids": insert.excluded.source_episode_ids,
                "embedding": insert.excluded.embedding,
                "updated_at": func.now(),
            },
        ).returning(SemanticFactRow)
        async with session_scope() as session:
            result = await session.execute(stmt)
            row = result.scalar_one()
            return _row_to_fact(row)

    async def recall(self, query: str, k: int = 5) -> list[SemanticFact]:
        """Recall the ``k`` facts most similar to ``query`` (cosine), ``score`` set."""
        if k <= 0:
            return []
        query_vector = await self._embedder.embed_one(query)
        async with session_scope() as session:
            candidates = await SemanticFactRepository(session).nearest(query_vector, k)
        return [
            _row_to_fact(row, score=similarity_from_distance(distance))
            for row, distance in candidates
        ]

    async def link(self, from_fact: int, to_fact: int, relation: str) -> SemanticEdge:
        """Create (idempotently) a labelled edge from one fact to another."""
        insert = pg_insert(SemanticEdgeRow).values(
            from_fact=from_fact, to_fact=to_fact, relation=relation
        )
        stmt = insert.on_conflict_do_nothing(
            index_elements=["from_fact", "to_fact", "relation"]
        )
        async with session_scope() as session:
            await session.execute(stmt)
            existing = await session.execute(
                select(SemanticEdgeRow).where(
                    SemanticEdgeRow.from_fact == from_fact,
                    SemanticEdgeRow.to_fact == to_fact,
                    SemanticEdgeRow.relation == relation,
                )
            )
            return _row_to_edge(existing.scalar_one())

    async def neighbours(
        self, fact_id: int, *, relation: str | None = None
    ) -> list[SemanticFact]:
        """Facts linked from ``fact_id`` (optionally filtered by relation)."""
        async with session_scope() as session:
            rows = await SemanticEdgeRepository(session).neighbours(fact_id, relation=relation)
            return [_row_to_fact(row) for row in rows]
