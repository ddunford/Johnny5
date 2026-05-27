"""Procedural memory: store, find-by-intent, outcome reinforcement (TC-1.4).

A skill is embedded as ``f"{name} {description}"`` so it is findable by intent.
Reinforcement keeps ``success_rate`` exact as ``successes / uses``; re-storing a
known skill refreshes its recipe but must preserve its reinforcement history
(relearning the recipe shouldn't erase how well it has worked).
"""

from __future__ import annotations

import pytest
from helpers.embeddings import DeterministicEmbedder, axis_vector
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.memory.procedural import ProceduralMemory, Skill

# Embedded text is ``f"{name} {description}".strip()``.
BOIL = axis_vector(0)


async def test_store_find_and_reinforce(memory_db: AsyncEngine) -> None:
    embedder = DeterministicEmbedder(
        {
            "boil water heat a kettle until it whistles": BOIL,
            "how do I make a hot drink?": BOIL,  # the find query, same intent axis
        }
    )
    store = ProceduralMemory(embedder)

    stored = await store.store(
        Skill(
            name="boil water",
            description="heat a kettle until it whistles",
            recipe={"steps": ["fill", "switch on", "wait"]},
        )
    )
    assert stored.id is not None
    assert stored.uses == 0
    assert stored.successes == 0
    assert stored.success_rate == pytest.approx(0.0)

    # found by intent similarity ...
    found = await store.find("how do I make a hot drink?", k=1)
    assert len(found) == 1
    assert found[0].name == "boil water"
    assert found[0].recipe == {"steps": ["fill", "switch on", "wait"]}
    assert found[0].score is not None and found[0].score == pytest.approx(1.0)

    # ... and reinforcement updates uses/successes/rate exactly.
    after_success = await store.reinforce(stored.id, success=True)
    assert after_success.uses == 1
    assert after_success.successes == 1
    assert after_success.success_rate == pytest.approx(1.0)

    after_failure = await store.reinforce(stored.id, success=False)
    assert after_failure.uses == 2
    assert after_failure.successes == 1
    assert after_failure.success_rate == pytest.approx(0.5)


async def test_restoring_a_skill_refreshes_recipe_but_keeps_history(memory_db: AsyncEngine) -> None:
    store = ProceduralMemory(DeterministicEmbedder())

    original = await store.store(Skill(name="greet", description="say hello", recipe={"v": 1}))
    assert original.id is not None
    await store.reinforce(original.id, success=True)

    relearned = await store.store(Skill(name="greet", description="say hi", recipe={"v": 2}))

    assert relearned.id == original.id  # same skill, updated in place
    assert relearned.recipe == {"v": 2}  # recipe refreshed
    assert relearned.uses == 1  # history preserved
    assert relearned.successes == 1
    assert relearned.success_rate == pytest.approx(1.0)


async def test_reinforce_unknown_skill_raises(memory_db: AsyncEngine) -> None:
    store = ProceduralMemory(DeterministicEmbedder())
    with pytest.raises(ValueError):
        await store.reinforce(999_999, success=True)


async def test_find_k_zero_returns_empty(memory_db: AsyncEngine) -> None:
    store = ProceduralMemory(DeterministicEmbedder())
    await store.store(Skill(name="noop", description="does nothing"))
    assert await store.find("anything", k=0) == []
