"""Episodic memory: write + read-back (TC-1.1) and hybrid recall ranking (TC-1.2).

The ranking test is the phase's centerpiece. It proves recall is *hybrid* — that
recency and salience genuinely re-rank results — by constructing a case pure
cosine similarity cannot decide: two episodes with the **identical** topic vector
(an exact similarity tie). Only the recency+salience blend can break the tie, and
it must rank the recent/important one first while excluding the unrelated one.

All embeddings are injected via ``DeterministicEmbedder`` so similarity is exact
and reproducible (never the live TEI server), and ``recall`` is given an explicit
``now`` so recency is deterministic — no wall-clock dependence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from helpers.embeddings import DeterministicEmbedder, axis_vector
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.memory.base import RecallWeights
from brain.memory.episodic import Episode, EpisodeRepository, EpisodicMemory
from foundation.db import session_scope

# Two orthogonal one-hot vectors: "on topic" (distance 0 → similarity 1.0 to the
# query) and "unrelated" (distance 1 → similarity 0.0).
TOPIC = axis_vector(0)
UNRELATED = axis_vector(1)

NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)

# ── TC-1.1: write + read-back ────────────────────────────────────────────────


async def test_write_persists_and_reads_back(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder({"the kettle finished boiling": TOPIC})
    store = EpisodicMemory(embedder)

    written = await store.write(
        Episode(
            kind="observation",
            content="the kettle finished boiling",
            actors=["johnny"],
            emotion_tags=["calm"],
            salience=0.7,
        )
    )

    # write returns the persisted identity + timestamp ...
    assert written.id is not None
    assert written.ts is not None
    # ... and embedded its content through the injected embedder (FC-4).
    assert embedder.calls == [["the kettle finished boiling"]]

    async with session_scope() as session:
        row = await EpisodeRepository(session).get(written.id)

    assert row is not None
    assert row.kind == "observation"
    assert row.content == "the kettle finished boiling"
    assert row.actors == ["johnny"]
    assert row.emotion_tags == ["calm"]
    assert row.salience == pytest.approx(0.7)
    # a real 1024-d embedding round-tripped (the one-hot TOPIC vector).
    assert len(row.embedding) == 1024
    assert row.embedding[0] == pytest.approx(1.0)
    assert all(component == pytest.approx(0.0) for component in row.embedding[1:])


async def test_salience_is_clamped_on_write(memory_db: AsyncEngine) -> None:
    store = EpisodicMemory(DeterministicEmbedder())
    written = await store.write(Episode(kind="observation", content="overflowing", salience=4.2))
    async with session_scope() as session:
        row = await EpisodeRepository(session).get(written.id)
    assert row is not None
    assert row.salience == pytest.approx(1.0)


# ── TC-1.2: hybrid recall ranking ────────────────────────────────────────────


def _gardening_embedder() -> DeterministicEmbedder:
    return DeterministicEmbedder(
        {
            "what do I know about gardening?": TOPIC,  # the query
            "I planted tomato seeds in spring": TOPIC,  # (a) similar, old, low salience
            "today I watered the seedlings": TOPIC,  # (b) similar, recent, high salience
            "the bus into town was late": UNRELATED,  # (c) unrelated
        }
    )


async def _seed_gardening_episodes(store: EpisodicMemory) -> None:
    # (a) on-topic but a week old and unimportant
    await store.write(
        Episode(
            kind="memory",
            content="I planted tomato seeds in spring",
            ts=NOW - timedelta(days=7),
            salience=0.1,
        )
    )
    # (b) on-topic, just now, and important
    await store.write(
        Episode(
            kind="memory",
            content="today I watered the seedlings",
            ts=NOW,
            salience=0.9,
        )
    )
    # (c) unrelated — also old and unimportant, so only similarity could have
    # rescued it, and it has none.
    await store.write(
        Episode(
            kind="memory",
            content="the bus into town was late",
            ts=NOW - timedelta(days=7),
            salience=0.1,
        )
    )


async def test_hybrid_recall_ranks_recent_salient_above_stale_and_excludes_unrelated(
    memory_db: AsyncEngine,
) -> None:
    store = EpisodicMemory(
        _gardening_embedder(),
        weights=RecallWeights(
            similarity=1.0, recency=1.0, salience=1.0, recency_halflife_seconds=3600.0
        ),
    )
    await _seed_gardening_episodes(store)

    results = await store.recall("what do I know about gardening?", k=2, now=NOW)
    contents = [episode.content for episode in results]

    # (b) the recent/important memory ranks above (a) the stale/trivial one —
    # the whole point of hybrid recall, since they tie on pure similarity.
    assert contents == ["today I watered the seedlings", "I planted tomato seeds in spring"]
    # (c) the unrelated memory is excluded from the top-k.
    assert "the bus into town was late" not in contents
    # the blend produced a strict ordering (no tie survived).
    assert results[0].score is not None and results[1].score is not None
    assert results[0].score > results[1].score


async def test_pure_similarity_alone_would_tie_a_and_b(memory_db: AsyncEngine) -> None:
    """The premise the ranking test rests on: with similarity-only weighting,
    (a) and (b) score identically — so recency+salience are what separate them."""
    store = EpisodicMemory(
        _gardening_embedder(),
        weights=RecallWeights(
            similarity=1.0, recency=0.0, salience=0.0, recency_halflife_seconds=3600.0
        ),
    )
    await _seed_gardening_episodes(store)

    results = await store.recall("what do I know about gardening?", k=3, now=NOW)
    score_by_content = {episode.content: episode.score for episode in results}

    a_score = score_by_content["I planted tomato seeds in spring"]
    b_score = score_by_content["today I watered the seedlings"]
    c_score = score_by_content["the bus into town was late"]
    assert a_score is not None and b_score is not None and c_score is not None

    assert a_score == pytest.approx(b_score)
    # and both clearly beat the unrelated one on similarity alone.
    assert c_score < b_score


async def test_recall_k_zero_returns_empty(memory_db: AsyncEngine) -> None:
    store = EpisodicMemory(_gardening_embedder())
    await _seed_gardening_episodes(store)
    assert await store.recall("what do I know about gardening?", k=0, now=NOW) == []
