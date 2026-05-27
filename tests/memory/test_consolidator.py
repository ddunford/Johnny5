"""Consolidation stub: episodic → semantic (TC-1.6).

This phase ships a naive, LLM-free pass (the real clustering+summarisation lands
Phase 4). The bar here is exactly the test plan's: ``run()`` must produce at
least one semantic fact that references the source episode ids and is recallable
through semantic memory. Quality is explicitly out of scope this phase.
"""

from __future__ import annotations

from helpers.embeddings import DeterministicEmbedder
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.memory.consolidator import Consolidator
from brain.memory.episodic import Episode, EpisodicMemory
from brain.memory.semantic import SemanticMemory


async def test_run_distils_episodes_into_a_recallable_fact_with_provenance(
    memory_db: AsyncEngine,
) -> None:
    embedder = DeterministicEmbedder()  # one cluster → recall returns it regardless of vector
    episodic = EpisodicMemory(embedder)
    e1 = await episodic.write(Episode(kind="observation", content="the lab lights flickered"))
    e2 = await episodic.write(Episode(kind="observation", content="the server fan got louder"))
    e3 = await episodic.write(Episode(kind="observation", content="a parcel arrived at the door"))

    semantic = SemanticMemory(embedder)
    facts = await Consolidator(semantic).run()

    assert len(facts) >= 1
    observation_fact = next(f for f in facts if f.subject == "observation")
    assert observation_fact.predicate == "recent_experience"
    # carries provenance: the episode ids it was distilled from.
    assert {e.id for e in (e1, e2, e3)} == set(observation_fact.source_episode_ids)

    # the consolidated fact is recallable through semantic memory ...
    recalled = await semantic.recall("what happened recently?", k=5)
    recalled_observation = next(
        f for f in recalled if f.subject == "observation" and f.predicate == "recent_experience"
    )
    # ... and still carries the source episode ids when recalled.
    assert {e.id for e in (e1, e2, e3)} == set(recalled_observation.source_episode_ids)


async def test_run_writes_one_fact_per_episode_kind(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder()
    episodic = EpisodicMemory(embedder)
    await episodic.write(Episode(kind="observation", content="something happened"))
    await episodic.write(Episode(kind="reflection", content="I wonder why"))

    facts = await Consolidator(SemanticMemory(embedder)).run()

    assert {f.subject for f in facts} == {"observation", "reflection"}


async def test_run_with_no_episodes_returns_empty(memory_db: AsyncEngine) -> None:
    assert await Consolidator(SemanticMemory(DeterministicEmbedder())).run() == []
