"""TC-6b.4 (the memory half) — ``WebReadConsolidator``: a web read becomes memory.

The headline curiosity loop is "drive → web tool → remember → ease"; this proves
the *remember* step in isolation (the drive→tool→ease wiring is the Deliberation
integration, TASK-6b.9). A web read is distilled into an episode (carrying the url)
plus a semantic fact whose provenance chains back to that episode, reusing the
``consolidation`` model contract — and a later recall surfaces it. DB-backed
(episode + semantic_fact), run in-network; a canned router stands in for inference.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest_asyncio
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from helpers.embeddings import DeterministicEmbedder, axis_vector
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.effectors.web_consolidator import WEB_READ_KIND, WebReadConsolidator
from brain.llm.base import Completion, LLMUnavailableError, Message
from brain.memory.consolidator import ConsolidationSummary
from brain.memory.episodic import EpisodicMemory
from brain.memory.semantic import SemanticMemory

_MEMORY_TABLES = ("semantic_edge", "semantic_fact", "skill", "episode")


# ── router + config doubles (duck-typed; the router contract is fixed) ──────────


class _ScriptedRouter:
    """Returns one canned ``ConsolidationSummary`` and records the role/schema used."""

    def __init__(self, summary: ConsolidationSummary) -> None:
        self._summary = summary
        self.roles: list[str] = []
        self.schemas: list[type | None] = []

    async def complete(
        self,
        role: str,
        messages: Sequence[Message],
        *,
        schema: type | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Completion:
        self.roles.append(role)
        self.schemas.append(schema)
        return Completion(
            content=self._summary.model_dump_json(), provider="canned", model="canned"
        )


class _TiredRouter:
    """Every call raises ``LLMUnavailableError`` (no provider available)."""

    async def complete(self, role: str, messages: Sequence[Message], **_kw: object) -> Completion:
        raise LLMUnavailableError(role)


class _StubConfigStore:
    """A config store double whose ``load_prompt`` always returns a non-empty prompt."""

    def load_prompt(self, name: str) -> str:
        return "Distil this web read into one durable fact."


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


async def test_consolidate_writes_episode_and_fact_with_url_provenance(
    memory_db: AsyncEngine,
) -> None:
    embedder = DeterministicEmbedder()
    summary = ConsolidationSummary(
        subject="Curiosity rover", predicate="is", object="still operating on Mars", confidence=0.7
    )
    router = _ScriptedRouter(summary)
    consolidator = WebReadConsolidator(
        episodic=EpisodicMemory(embedder),
        semantic=SemanticMemory(embedder),
        router=router,  # type: ignore[arg-type]  # duck-typed router double
        config_store=_StubConfigStore(),  # type: ignore[arg-type]  # duck-typed config double
    )

    result = await consolidator.consolidate(
        url="https://example.com/mars",
        title="Mars rover update",
        text="The Curiosity rover continues to explore Gale crater after many years.",
    )

    # The read was distilled via the consolidation role + schema.
    assert result.summarised is True
    assert router.roles == ["consolidation"]
    assert router.schemas == [ConsolidationSummary]

    # An episode recorded the read, carrying the url (the provenance anchor).
    assert result.episode.kind == WEB_READ_KIND
    assert "https://example.com/mars" in result.episode.content
    assert result.episode.id is not None

    # The fact is the summariser's output, with provenance chaining to that episode.
    assert (result.fact.subject, result.fact.predicate) == ("Curiosity rover", "is")
    assert result.fact.source_episode_ids == [result.episode.id]


async def test_recall_surfaces_the_consolidated_fact(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder()
    summary = ConsolidationSummary(
        subject="Mars", predicate="has", object="an operating rover", confidence=0.8
    )
    # Pin the fact text + query to one axis so recall is exact.
    embedder.set("Mars has an operating rover", axis_vector(3))
    embedder.set("mars rover", axis_vector(3))

    consolidator = WebReadConsolidator(
        episodic=EpisodicMemory(embedder),
        semantic=SemanticMemory(embedder),
        router=_ScriptedRouter(summary),  # type: ignore[arg-type]
        config_store=_StubConfigStore(),  # type: ignore[arg-type]
    )
    result = await consolidator.consolidate(
        url="https://example.com/mars", title="Mars", text="rover news"
    )

    recalled = await SemanticMemory(embedder).recall("mars rover", k=5)
    assert any(
        f.subject == "Mars" and f.source_episode_ids == [result.episode.id] for f in recalled
    )


async def test_consolidate_without_router_writes_a_fallback_fact(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder()
    consolidator = WebReadConsolidator(
        episodic=EpisodicMemory(embedder),
        semantic=SemanticMemory(embedder),
        router=None,  # no inference available
    )

    result = await consolidator.consolidate(
        url="https://example.com/x", title="A Title", text="some readable body text"
    )

    assert result.summarised is False
    # The fallback still writes a fact (low confidence) with provenance — the read is
    # remembered even when tired, so the loop never wedges.
    assert result.fact.subject == "A Title"
    assert result.fact.confidence < 0.5
    assert result.fact.source_episode_ids == [result.episode.id]


async def test_tired_router_degrades_to_fallback(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder()
    consolidator = WebReadConsolidator(
        episodic=EpisodicMemory(embedder),
        semantic=SemanticMemory(embedder),
        router=_TiredRouter(),  # type: ignore[arg-type]
        config_store=_StubConfigStore(),  # type: ignore[arg-type]
    )

    result = await consolidator.consolidate(url="https://e.com/a", title="T", text="body")

    assert result.summarised is False  # attempted the LLM, fell back gracefully
    assert result.fact.source_episode_ids == [result.episode.id]


async def test_episode_excerpt_is_bounded(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder()
    consolidator = WebReadConsolidator(
        episodic=EpisodicMemory(embedder),
        semantic=SemanticMemory(embedder),
        router=None,
    )
    huge = "x" * 50_000

    result = await consolidator.consolidate(url="https://e.com/big", title="Big", text=huge)

    # The autobiography episode holds a bounded excerpt, not the whole page (Attention
    # is a bottleneck) — well under the raw text size.
    assert len(result.episode.content) < 2_500
