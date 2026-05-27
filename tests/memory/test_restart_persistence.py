"""Persistence across restart + snapshot/restore fidelity (TC-1.7 → TASK-1.9).

Two guarantees, both required for continuity (FC-6):

  1. Postgres-backed memory survives a process restart — written episodes/facts/
     skills are still recallable after every engine/connection is torn down and
     rebuilt from scratch (the in-process stand-in for ``./ctl.sh down && up``).
  2. A snapshot restores into a *clean* database **identically** — same ids,
     content, embeddings, edges, skills, and working-memory items, down to the
     row. Restore is destructive+idempotent, so the restored state must equal the
     pre-snapshot state exactly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from helpers.clock import FrozenClock
from helpers.embeddings import DeterministicEmbedder, axis_vector
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.memory.episodic import Episode, EpisodeRepository, EpisodeRow, EpisodicMemory
from brain.memory.procedural import ProceduralMemory, Skill, SkillRow
from brain.memory.semantic import SemanticEdgeRow, SemanticFactRow, SemanticMemory
from brain.memory.snapshot import MemorySnapshot
from brain.memory.working import WorkingMemory, WorkingMemoryItem
from foundation.db import session_scope

_MEMORY_TABLES = ("episode", "semantic_fact", "semantic_edge", "skill")
# Snapshot v2 also carries identity + drive/mood/goal. ``memory_db`` only cleans the
# memory spine, and migrations *seed* identity/drive_state — so these tests must clear
# the v2 stores themselves, or a v2 ``snapshot()`` would capture a non-deterministic
# number of seeded rows (an order-dependent flake). Cleared to empty so the v2 counts
# are a deterministic 0 here; the memory round-trip is what these tests assert.
_V2_TABLES = ("identity", "goal", "drive_state", "mood")
_ALL_TABLES = (*_MEMORY_TABLES, *_V2_TABLES)


# ── 1. survives a simulated restart ──────────────────────────────────────────


async def test_postgres_memory_survives_a_simulated_restart(
    memory_db: AsyncEngine,
    simulate_restart: Callable[[], Awaitable[AsyncEngine]],
) -> None:
    embedder = DeterministicEmbedder(
        {"what was I drinking?": axis_vector(0), "I brewed a pot of tea": axis_vector(0)}
    )
    written = await EpisodicMemory(embedder).write(
        Episode(kind="observation", content="I brewed a pot of tea", salience=0.6)
    )

    # tear down every engine/connection and reconnect — only on-disk data survives.
    await simulate_restart()

    recalled = await EpisodicMemory(embedder).recall(
        "what was I drinking?", k=1, now=datetime.now(UTC)
    )
    assert len(recalled) == 1
    assert recalled[0].id == written.id
    assert recalled[0].content == "I brewed a pot of tea"
    assert recalled[0].salience == 0.6


# ── 2. snapshot restores into a clean DB identically ─────────────────────────


async def _dump_state(working: WorkingMemory) -> dict[str, Any]:
    """A fully-comparable projection of all memory state (order-stable)."""
    async with session_scope() as session:
        episodes = [
            (r.id, r.kind, r.content, r.salience, [float(x) for x in r.embedding])
            for r in (await session.execute(select(EpisodeRow).order_by(EpisodeRow.id)))
            .scalars()
            .all()
        ]
        facts = [
            (r.id, r.subject, r.predicate, r.object, r.confidence, list(r.source_episode_ids))
            for r in (await session.execute(select(SemanticFactRow).order_by(SemanticFactRow.id)))
            .scalars()
            .all()
        ]
        edges = [
            (r.id, r.from_fact, r.to_fact, r.relation)
            for r in (await session.execute(select(SemanticEdgeRow).order_by(SemanticEdgeRow.id)))
            .scalars()
            .all()
        ]
        skills = [
            (r.id, r.name, dict(r.recipe), r.uses, r.successes, r.success_rate)
            for r in (await session.execute(select(SkillRow).order_by(SkillRow.id))).scalars().all()
        ]
    working_items = sorted((i.id, i.content, i.salience) for i in await working.export_items())
    return {
        "episodes": episodes,
        "facts": facts,
        "edges": edges,
        "skills": skills,
        "working": working_items,
    }


async def _truncate_all() -> None:
    async with session_scope() as session:
        for table in reversed(_ALL_TABLES):
            await session.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))


async def test_snapshot_restores_into_a_clean_db_identically(
    memory_db: AsyncEngine, redis_client: Redis, tmp_path: Path
) -> None:
    embedder = DeterministicEmbedder()
    working = WorkingMemory(redis=redis_client, clock=FrozenClock(start=1_000.0))

    # Clear the v2 stores (migration-seeded identity/drives) so the v2 snapshot below
    # captures a deterministic empty state regardless of test order.
    await _truncate_all()

    # seed all four persistent stores + working memory
    await EpisodicMemory(embedder).write(Episode(kind="observation", content="alpha", salience=0.4))
    semantic = SemanticMemory(embedder)
    sky = await semantic.upsert_fact("sky", "has-colour", "blue", confidence=0.9)
    grass = await semantic.upsert_fact("grass", "has-colour", "green")
    assert sky.id is not None and grass.id is not None
    await semantic.link(sky.id, grass.id, "contrasts-with")
    await ProceduralMemory(embedder).store(
        Skill(name="boil water", description="use the kettle", recipe={"steps": ["fill", "on"]})
    )
    await working.put(WorkingMemoryItem(content="current focus", salience=0.7), ttl=0)

    before = await _dump_state(working)

    snapshotter = MemorySnapshot(root=tmp_path, working=working)
    snapshot_path = await snapshotter.snapshot(label="t1")

    # wipe to a genuinely clean DB + empty working set, and prove it's empty ...
    await _truncate_all()
    await working.clear()
    assert await _dump_state(working) == {
        "episodes": [],
        "facts": [],
        "edges": [],
        "skills": [],
        "working": [],
    }

    # ... then restore and assert the state is reproduced down to the row. A v2
    # snapshot returns the 9-key counts shape (memory spine + identity/drive/mood/goal,
    # all 0 here since only the memory stores were seeded).
    counts = await snapshotter.restore(snapshot_path)
    assert counts == {
        "episodes": 1,
        "semantic_facts": 2,
        "semantic_edges": 1,
        "skills": 1,
        "working_memory": 1,
        "identity": 0,
        "drive_state": 0,
        "mood": 0,
        "goal": 0,
    }
    assert await _dump_state(working) == before


async def test_restore_is_idempotent(
    memory_db: AsyncEngine, redis_client: Redis, tmp_path: Path
) -> None:
    embedder = DeterministicEmbedder()
    working = WorkingMemory(redis=redis_client, clock=FrozenClock(start=1_000.0))
    await EpisodicMemory(embedder).write(Episode(kind="observation", content="only one"))
    await working.put(WorkingMemoryItem(content="focus", salience=0.5), ttl=0)

    snapshotter = MemorySnapshot(root=tmp_path, working=working)
    path = await snapshotter.snapshot(label="once")
    expected = await _dump_state(working)

    # restoring twice (replace=True truncates first) must not duplicate rows.
    await snapshotter.restore(path)
    await snapshotter.restore(path)

    after = await _dump_state(working)
    assert after == expected
    async with session_scope() as session:
        episode_count = len(
            (await session.execute(select(EpisodeRepository.model))).scalars().all()
        )
    assert episode_count == 1
