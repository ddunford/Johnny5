"""Snapshot v2: identity + drive/mood/goal round-trip, v1 back-compat (TC-4.7).

Phase 4 extends the P1 snapshot to **v2**: a backup now carries Johnny's *identity*
(every self-model version) and *motivational state* (drive/mood/goal) alongside the
memory spine, so a restore reproduces *who he is*, not just *what he remembers*
(FC-6). Two guarantees:

* a v2 snapshot **round-trips into a clean DB identically** — ids + identity versions
  + drive values + mood + goals reproduced down to the row;
* a **v1 snapshot still restores** (back-compat): the version-branched restore loads
  the memory spine and simply skips the absent identity/state stores.

Plus: the snapshot lives under the gitignored ``snapshots/`` tree, and an unknown
version fails loudly. DB-backed (uses the broad ``sleep_db`` scope) + the test Redis
for working memory → run in-network via ``./ctl.sh test``. The unsupported-version
guard is pure (raises before any I/O) and runs host-side.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from helpers.clock import FrozenClock
from helpers.db import truncate_tables
from helpers.embeddings import DeterministicEmbedder
from helpers.phase4 import SLEEP_TABLES
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.affect.agent import MoodRow
from brain.drives.engine import DriveEngine, DriveStateRow
from brain.goals.store import GoalRow
from brain.memory.episodic import Episode, EpisodeRow, EpisodicMemory
from brain.memory.snapshot import SNAPSHOT_VERSION, MemorySnapshot
from brain.memory.working import WorkingMemory, WorkingMemoryItem
from brain.self_model.store import IdentityDoc, IdentityRow, IdentityStore
from foundation.config import get_settings
from foundation.db import session_scope

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


async def _seed_mood(*, valence: float, arousal: float, emotions: dict[str, float]) -> None:
    async with session_scope() as session:
        session.add(MoodRow(ts=_T0, valence=valence, arousal=arousal, emotions=emotions))


async def _seed_goal(*, source: str, description: str, status: str) -> None:
    async with session_scope() as session:
        session.add(
            GoalRow(
                source=source,
                description=description,
                status=status,
                priority=0.5,
                created_at=_T0,
            )
        )


async def _dump_state(working: WorkingMemory) -> dict[str, Any]:
    """A fully-comparable projection of all v2 state (order-stable)."""
    async with session_scope() as session:
        episodes = [
            (r.id, r.content, r.salience)
            for r in (await session.execute(select(EpisodeRow).order_by(EpisodeRow.id)))
            .scalars()
            .all()
        ]
        identities = [
            (r.id, r.name, r.version, r.self_model_doc, list(r.values), dict(r.relationships))
            for r in (await session.execute(select(IdentityRow).order_by(IdentityRow.version)))
            .scalars()
            .all()
        ]
        drives = sorted(
            (r.drive, r.value, r.threshold)
            for r in (await session.execute(select(DriveStateRow))).scalars().all()
        )
        moods = [
            (r.id, r.valence, r.arousal, dict(r.emotions))
            for r in (await session.execute(select(MoodRow).order_by(MoodRow.id))).scalars().all()
        ]
        goals = [
            (r.id, r.source, r.description, r.status)
            for r in (await session.execute(select(GoalRow).order_by(GoalRow.id))).scalars().all()
        ]
    working_items = sorted((i.id, i.content, i.salience) for i in await working.export_items())
    return {
        "episodes": episodes,
        "identities": identities,
        "drives": drives,
        "moods": moods,
        "goals": goals,
        "working": working_items,
    }


async def test_snapshot_v2_round_trips_identity_and_motivational_state(
    sleep_db: AsyncEngine, redis_client: Redis, tmp_path: Path
) -> None:
    embedder = DeterministicEmbedder()
    working = WorkingMemory(redis=redis_client, clock=FrozenClock(start=1_000.0))

    # Seed the memory spine + identity (two versions) + drive/mood/goal + working.
    await EpisodicMemory(embedder).write(Episode(kind="observation", content="alpha", salience=0.4))
    store = IdentityStore()
    await store.ensure_seeded()  # v1 from the anchor
    await store.append(
        IdentityDoc(
            name="Johnny",
            self_model_doc="v2 — I am becoming more attentive.",
            values=["stay alive", "keep learning"],
            concerns=["the rig"],
            relationships={"Dan": "my creator"},
        )
    )  # v2
    await DriveEngine().bootstrap()  # seeds the seven drives
    await _seed_mood(valence=0.3, arousal=0.6, emotions={"contentment": 0.4})
    await _seed_goal(source="curiosity", description="learn about the rig", status="active")
    await working.put(WorkingMemoryItem(content="current focus", salience=0.7), ttl=0)

    before = await _dump_state(working)
    assert len(before["identities"]) == 2  # both versions present

    snapshotter = MemorySnapshot(root=tmp_path, working=working)
    path = await snapshotter.snapshot(label="v2t1")

    # It's a v2 snapshot carrying the new stores.
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["version"] == SNAPSHOT_VERSION == 2
    assert manifest["counts"]["identity"] == 2
    assert manifest["counts"]["drive_state"] == 7
    assert manifest["counts"]["mood"] == 1
    assert manifest["counts"]["goal"] == 1
    for fname in ("identity.jsonl", "drive_state.jsonl", "mood.jsonl", "goal.jsonl"):
        assert (path / fname).exists()

    # Wipe to a genuinely clean DB + working set, prove it's empty ...
    await truncate_tables(SLEEP_TABLES)
    await working.clear()
    empty = await _dump_state(working)
    assert empty == {
        "episodes": [],
        "identities": [],
        "drives": [],
        "moods": [],
        "goals": [],
        "working": [],
    }

    # ... then restore and assert every store is reproduced down to the row.
    counts = await snapshotter.restore(path)
    assert counts["identity"] == 2
    assert counts["drive_state"] == 7
    assert counts["mood"] == 1
    assert counts["goal"] == 1
    assert await _dump_state(working) == before


async def test_v1_snapshot_still_restores(
    sleep_db: AsyncEngine, redis_client: Redis, tmp_path: Path
) -> None:
    """Back-compat: a v1 snapshot (memory only, no identity/state files) restores its
    memory spine via the version-branched restore — the v2 stores stay empty, no crash."""
    embedder = DeterministicEmbedder()
    working = WorkingMemory(redis=redis_client, clock=FrozenClock(start=1_000.0))
    await EpisodicMemory(embedder).write(
        Episode(kind="observation", content="from v1", salience=0.5)
    )

    # Make a real snapshot, then downgrade it to a v1 on-disk shape: manifest v1 +
    # remove the v2-only files (exactly what a Phase-1 snapshot would have written).
    snapshotter = MemorySnapshot(root=tmp_path, working=working)
    path = await snapshotter.snapshot(label="legacy")
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["version"] = 1
    manifest["counts"] = {
        k: v
        for k, v in manifest["counts"].items()
        if k not in {"identity", "drive_state", "mood", "goal"}
    }
    (path / "manifest.json").write_text(json.dumps(manifest))
    for fname in ("identity.jsonl", "drive_state.jsonl", "mood.jsonl", "goal.jsonl"):
        (path / fname).unlink()

    await truncate_tables(SLEEP_TABLES)
    await working.clear()

    counts = await snapshotter.restore(path)

    # The memory spine came back ...
    assert counts == {
        "episodes": 1,
        "semantic_facts": 0,
        "semantic_edges": 0,
        "skills": 0,
        "working_memory": 0,
    }
    assert "identity" not in counts  # v1 restore doesn't touch the v2 stores
    state = await _dump_state(working)
    assert [e[1] for e in state["episodes"]] == ["from v1"]
    assert state["identities"] == []  # v2 stores untouched by a v1 restore
    assert state["drives"] == []


async def test_unsupported_snapshot_version_fails_loudly(tmp_path: Path) -> None:
    """An unknown snapshot version raises before touching the DB — no half-restore."""
    snap = tmp_path / "future"
    snap.mkdir()
    (snap / "manifest.json").write_text(json.dumps({"version": 99, "counts": {}}))

    with pytest.raises(ValueError):
        await MemorySnapshot(root=tmp_path).restore(snap)


def test_default_snapshot_root_is_under_the_gitignored_snapshots_tree() -> None:
    """Backups land under the gitignored ``snapshots/`` tree — Johnny's identity +
    memory are private and never pushed (FC-6 / CLAUDE.md privacy)."""
    assert get_settings().memory_snapshot_dir.startswith("snapshots/")
