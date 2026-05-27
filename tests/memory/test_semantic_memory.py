"""Semantic memory: facts, similarity recall, graph edges (TC-1.3).

Facts are consolidated knowledge (no recency weighting — that's episodic), so
recall here is pure cosine similarity. The store embeds the ``subject predicate
object`` triple, so deterministic vectors are pinned to those exact strings. The
test also nails the upsert contract: re-asserting a ``(subject, predicate)``
updates the row in place rather than duplicating it.
"""

from __future__ import annotations

import pytest
from helpers.embeddings import DeterministicEmbedder, axis_vector
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.memory.semantic import SemanticEdgeRow, SemanticFactRow, SemanticMemory
from foundation.db import session_scope

# Embedded text is ``f"{subject} {predicate} {object}"`` (see ``_fact_text``).
SKY = axis_vector(0)
GRASS = axis_vector(1)


async def test_upsert_recall_and_edge_traversal(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder(
        {
            "sky has-colour blue": SKY,
            "grass has-colour green": GRASS,
            "what colour is the sky?": SKY,  # the query, on the sky axis
        }
    )
    store = SemanticMemory(embedder)

    sky = await store.upsert_fact("sky", "has-colour", "blue", confidence=0.9)
    grass = await store.upsert_fact("grass", "has-colour", "green", confidence=0.8)
    assert sky.id is not None and grass.id is not None

    # recall ranks the similar fact first ...
    results = await store.recall("what colour is the sky?", k=2)
    assert results[0].subject == "sky"
    assert results[0].object == "blue"
    assert results[0].confidence == pytest.approx(0.9)
    assert results[0].score is not None and results[0].score == pytest.approx(1.0)

    # ... and the edge links to the other fact.
    edge = await store.link(sky.id, grass.id, "contrasts-with")
    assert edge.from_fact == sky.id
    assert edge.to_fact == grass.id
    assert edge.relation == "contrasts-with"

    neighbours = await store.neighbours(sky.id)
    assert [fact.subject for fact in neighbours] == ["grass"]
    # relation filter narrows the traversal.
    assert [f.subject for f in await store.neighbours(sky.id, relation="contrasts-with")] == [
        "grass"
    ]
    assert await store.neighbours(sky.id, relation="no-such-relation") == []


async def test_link_is_idempotent(memory_db: AsyncEngine) -> None:
    store = SemanticMemory(DeterministicEmbedder())
    a = await store.upsert_fact("a", "rel", "x")
    b = await store.upsert_fact("b", "rel", "y")
    assert a.id is not None and b.id is not None

    first = await store.link(a.id, b.id, "knows")
    again = await store.link(a.id, b.id, "knows")
    assert again.id == first.id  # same edge, not a duplicate

    async with session_scope() as session:
        edge_count = (
            await session.execute(select(func.count()).select_from(SemanticEdgeRow))
        ).scalar_one()
    assert edge_count == 1


async def test_reasserting_a_fact_updates_in_place_not_duplicates(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder(
        {"sky has-colour blue": SKY, "sky has-colour grey": axis_vector(2)}
    )
    store = SemanticMemory(embedder)

    first = await store.upsert_fact("sky", "has-colour", "blue", confidence=0.5)
    updated = await store.upsert_fact("sky", "has-colour", "grey", confidence=0.7)

    # same row, new object/confidence — never a second (sky, has-colour) row.
    assert updated.id == first.id
    assert updated.object == "grey"
    assert updated.confidence == pytest.approx(0.7)

    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(SemanticFactRow).where(
                        SemanticFactRow.subject == "sky", SemanticFactRow.predicate == "has-colour"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].object == "grey"
