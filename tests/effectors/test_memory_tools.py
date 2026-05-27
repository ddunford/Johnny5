"""TC-6b.8 — ``memory_write`` + ``memory_search`` (Johnny's memory as tools).

DB-backed (the tools wrap the real episodic/semantic spine), run in-network via
``./ctl.sh test``. A deterministic embedder pins the vectors so recall ranking is
exact: we prove a written memory persists and that a search surfaces both the
episode and a consolidated fact, ranked. Arg-validation is the pure, typed path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from helpers.embeddings import DeterministicEmbedder, axis_vector
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.effectors.memory_tools import (
    MemorySearchArgs,
    MemorySearchTool,
    MemoryWriteArgs,
    MemoryWriteTool,
)
from brain.memory.episodic import EpisodicMemory
from brain.memory.semantic import SemanticMemory

# FK-safe truncation order (edges → facts → episodes).
_MEMORY_TABLES = ("semantic_edge", "semantic_fact", "skill", "episode")


@pytest_asyncio.fixture
async def memory_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean memory schema on a fresh, loop-local global engine."""
    engine = install_fresh_global_engine()
    await truncate_tables(_MEMORY_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(_MEMORY_TABLES)
        await dispose_global_engine()


async def test_memory_write_persists_an_episode(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder()
    tool = MemoryWriteTool(episodic=EpisodicMemory(embedder=embedder))

    result = await tool.run(MemoryWriteArgs(content="I read that Curiosity is still roving Mars."))

    assert result.success is True
    assert result.output["id"] is not None
    assert result.output["kind"] == "self_memory"

    recent = await EpisodicMemory(embedder=embedder).recent(10)
    assert any("Curiosity is still roving Mars" in e.content for e in recent)


async def test_memory_search_surfaces_episode_and_fact(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder()
    # Pin the stored content, the fact text, and the query to the same axis so cosine
    # is exact (1.0) and the ranking is deterministic.
    embedder.set("Mars rovers are fascinating", axis_vector(1))
    embedder.set("Mars has rovers", axis_vector(1))  # _fact_text = "subject predicate object"
    embedder.set("mars", axis_vector(1))

    await MemoryWriteTool(episodic=EpisodicMemory(embedder=embedder)).run(
        MemoryWriteArgs(content="Mars rovers are fascinating")
    )
    await SemanticMemory(embedder=embedder).upsert_fact("Mars", "has", "rovers", confidence=0.8)

    search = MemorySearchTool(
        episodic=EpisodicMemory(embedder=embedder),
        semantic=SemanticMemory(embedder=embedder),
    )
    result = await search.run(MemorySearchArgs(query="mars", k=5))

    assert result.success is True
    episodes = result.output["episodes"]
    facts = result.output["facts"]
    assert isinstance(episodes, list) and len(episodes) == 1
    assert episodes[0]["content"] == "Mars rovers are fascinating"
    assert episodes[0]["score"] is not None  # search results are ranked
    assert isinstance(facts, list) and len(facts) == 1
    assert facts[0]["subject"] == "Mars"
    assert facts[0]["object"] == "rovers"


async def test_memory_search_empty_when_nothing_matches(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder()
    search = MemorySearchTool(
        episodic=EpisodicMemory(embedder=embedder),
        semantic=SemanticMemory(embedder=embedder),
    )
    result = await search.run(MemorySearchArgs(query="anything", k=5))

    assert result.success is True
    assert result.output["episodes"] == []
    assert result.output["facts"] == []


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({}, id="missing-content"),
        pytest.param({"content": ""}, id="empty-content"),
        pytest.param({"content": "x", "salience": 1.5}, id="salience-out-of-range"),
        pytest.param({"content": "x", "extra": "no"}, id="unknown-field"),
    ],
)
def test_bad_memory_write_args_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MemoryWriteArgs.model_validate(bad)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({}, id="missing-query"),
        pytest.param({"query": ""}, id="empty-query"),
        pytest.param({"query": "x", "k": 0}, id="k-too-small"),
        pytest.param({"query": "x", "k": 99}, id="k-too-large"),
        pytest.param({"query": "x", "extra": "no"}, id="unknown-field"),
    ],
)
def test_bad_memory_search_args_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MemorySearchArgs.model_validate(bad)
