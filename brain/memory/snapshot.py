"""Memory snapshot / restore — the continuity primitive (FC-6).

Johnny's continuity (the safety backups *and* the Continuity drive) depends on his
state surviving restarts and being restorable into a clean database. The
snapshot/restore primitive shipped in Phase 1 (the memory spine); Phase 4 schedules
it as part of sleep and **extends it to v2** so a backup also carries his *identity*
and *motivational state*, not just his memory.

A snapshot is a directory under the gitignored ``snapshots/`` tree:

    <root>/<label>/
        manifest.json          # version, created_at, per-store counts
        episodes.jsonl
        semantic_facts.jsonl
        semantic_edges.jsonl
        skills.jsonl
        working_memory.jsonl
        identity.jsonl         # v2+
        drive_state.jsonl      # v2+
        mood.jsonl             # v2+
        goal.jsonl             # v2+

The on-disk format is **stable** and versioned by ``SNAPSHOT_VERSION``. v2 adds the
``identity`` / ``drive_state`` / ``mood`` / ``goal`` stores; ``restore`` is
**version-branched** so a v1 backup (no identity/state files) still restores its
memory exactly. Embeddings are plain float lists; primary keys are preserved so a
restore reproduces the store identically (serial sequences are re-synced after).
Restore is destructive by default (``replace=True`` truncates first) so it is
idempotent — except ``mood``, which is *deleted* rather than truncated so the FK
from ``thought`` (``ON DELETE SET NULL``) doesn't cascade away the monologue log.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from brain.affect.agent import MoodRow
from brain.drives.engine import DriveStateRow
from brain.goals.store import GoalRow
from brain.memory.episodic import EpisodeRow
from brain.memory.procedural import SkillRow
from brain.memory.semantic import SemanticEdgeRow, SemanticFactRow
from brain.memory.working import WorkingMemory, WorkingMemoryItem
from brain.self_model.store import IdentityRow
from foundation.config import get_settings
from foundation.db import session_scope
from foundation.observability import get_logger

_log = get_logger("brain.memory.snapshot")

SNAPSHOT_VERSION = 2

_EPISODES = "episodes.jsonl"
_FACTS = "semantic_facts.jsonl"
_EDGES = "semantic_edges.jsonl"
_SKILLS = "skills.jsonl"
_WORKING = "working_memory.jsonl"
_MANIFEST = "manifest.json"
# v2 additions — identity + motivational state.
_IDENTITY = "identity.jsonl"
_DRIVES = "drive_state.jsonl"
_MOOD = "mood.jsonl"
_GOALS = "goal.jsonl"

# Memory tables (v1), in FK-safe restore order (facts before the edges that ref them).
_MEMORY_TABLES = ("episode", "semantic_fact", "semantic_edge", "skill")
# v2 identity/state tables with no inbound FK — safe to TRUNCATE CASCADE.
_V2_TRUNCATE_TABLES = ("identity", "goal", "drive_state")
# Tables with a serial ``id`` whose sequence must be re-synced after a PK-preserving
# restore (drive_state is keyed by ``drive`` (string) — no sequence).
_SERIAL_TABLES = (*_MEMORY_TABLES, "identity", "mood", "goal")


class MemorySnapshot:
    """Dump every persistent store to disk and reload it.

    ``root`` defaults to ``Settings.memory_snapshot_dir`` (gitignored). ``working``
    is injectable so the same Redis instance/namespace is snapshotted as used live.
    """

    def __init__(self, *, root: Path | None = None, working: WorkingMemory | None = None) -> None:
        settings = get_settings()
        self._root = root or Path(settings.memory_snapshot_dir)
        self._working = working or WorkingMemory()

    async def snapshot(self, *, label: str | None = None) -> Path:
        """Write a full v2 snapshot (memory + identity + drive/mood/goal) and return its dir."""
        stamp = label or datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = self._root / stamp
        target.mkdir(parents=True, exist_ok=True)

        async with session_scope() as session:
            episodes = list((await session.execute(select(EpisodeRow))).scalars().all())
            facts = list((await session.execute(select(SemanticFactRow))).scalars().all())
            edges = list((await session.execute(select(SemanticEdgeRow))).scalars().all())
            skills = list((await session.execute(select(SkillRow))).scalars().all())
            identities = list((await session.execute(select(IdentityRow))).scalars().all())
            drives = list((await session.execute(select(DriveStateRow))).scalars().all())
            moods = list((await session.execute(select(MoodRow))).scalars().all())
            goals = list((await session.execute(select(GoalRow))).scalars().all())

        _write_jsonl(target / _EPISODES, (_episode_dict(r) for r in episodes))
        _write_jsonl(target / _FACTS, (_fact_dict(r) for r in facts))
        _write_jsonl(target / _EDGES, (_edge_dict(r) for r in edges))
        _write_jsonl(target / _SKILLS, (_skill_dict(r) for r in skills))
        _write_jsonl(target / _IDENTITY, (_identity_dict(r) for r in identities))
        _write_jsonl(target / _DRIVES, (_drive_dict(r) for r in drives))
        _write_jsonl(target / _MOOD, (_mood_dict(r) for r in moods))
        _write_jsonl(target / _GOALS, (_goal_dict(r) for r in goals))

        working_items = await self._working.export_items()
        _write_jsonl(target / _WORKING, (i.model_dump() for i in working_items))

        counts = {
            "episodes": len(episodes),
            "semantic_facts": len(facts),
            "semantic_edges": len(edges),
            "skills": len(skills),
            "working_memory": len(working_items),
            "identity": len(identities),
            "drive_state": len(drives),
            "mood": len(moods),
            "goal": len(goals),
        }
        manifest = {
            "version": SNAPSHOT_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "counts": counts,
        }
        (target / _MANIFEST).write_text(json.dumps(manifest, indent=2))
        _log.info("memory.snapshot.written", path=str(target), counts=counts)
        return target

    async def restore(self, path: str | Path, *, replace: bool = True) -> dict[str, int]:
        """Load a snapshot back into the stores; return per-store row counts.

        Version-branched: a v1 snapshot restores the memory spine + working set
        (as Phase 1 did); a v2 snapshot also restores identity + drive/mood/goal.
        With ``replace`` (default) the relevant tables are cleared first so a
        restore into a clean DB reproduces it exactly and re-restoring is
        idempotent; primary keys are preserved and serial sequences re-synced.
        """
        target = Path(path)
        manifest = json.loads((target / _MANIFEST).read_text())
        version = manifest.get("version")
        if version not in (1, SNAPSHOT_VERSION):
            raise ValueError(
                f"unsupported snapshot version {version!r} "
                f"(this build reads v1–v{SNAPSHOT_VERSION})"
            )

        episodes = _read_jsonl(target / _EPISODES)
        facts = _read_jsonl(target / _FACTS)
        edges = _read_jsonl(target / _EDGES)
        skills = _read_jsonl(target / _SKILLS)
        working_items = _read_jsonl(target / _WORKING)
        # v2 stores — absent (→ empty) in a v1 snapshot.
        identities = _read_jsonl(target / _IDENTITY)
        drives = _read_jsonl(target / _DRIVES)
        moods = _read_jsonl(target / _MOOD)
        goals = _read_jsonl(target / _GOALS)

        is_v2 = version == SNAPSHOT_VERSION
        async with session_scope() as session:
            if replace:
                await self._clear(session, restore_v2=is_v2)
            session.add_all(EpisodeRow(**_episode_row(d)) for d in episodes)
            session.add_all(SemanticFactRow(**_fact_row(d)) for d in facts)
            await session.flush()  # facts must exist before edges (FK)
            session.add_all(SemanticEdgeRow(**d) for d in edges)
            session.add_all(SkillRow(**_skill_row(d)) for d in skills)
            if is_v2:
                session.add_all(IdentityRow(**_identity_row(d)) for d in identities)
                session.add_all(DriveStateRow(**_drive_row(d)) for d in drives)
                session.add_all(MoodRow(**_mood_row(d)) for d in moods)
                session.add_all(GoalRow(**_goal_row(d)) for d in goals)
            await session.flush()
            await _resync_sequences(session, restore_v2=is_v2)

        if replace:
            await self._working.clear()
        await self._working.import_items(WorkingMemoryItem(**d) for d in working_items)

        counts = {
            "episodes": len(episodes),
            "semantic_facts": len(facts),
            "semantic_edges": len(edges),
            "skills": len(skills),
            "working_memory": len(working_items),
        }
        if is_v2:
            counts |= {
                "identity": len(identities),
                "drive_state": len(drives),
                "mood": len(moods),
                "goal": len(goals),
            }
        _log.info("memory.snapshot.restored", path=str(target), version=version, counts=counts)
        return counts

    @staticmethod
    async def _clear(session: AsyncSession, *, restore_v2: bool) -> None:
        """Clear the tables a restore overwrites, FK-safely.

        Memory tables (and the no-inbound-FK v2 tables) are truncated with
        ``RESTART IDENTITY CASCADE``. ``mood`` is *deleted* instead so the
        ``thought.mood_id`` FK (``ON DELETE SET NULL``) nulls those references
        rather than cascading the thought log away.
        """
        for table in reversed(_MEMORY_TABLES):
            await session.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))
        if restore_v2:
            for table in _V2_TRUNCATE_TABLES:
                await session.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))
            await session.execute(text("DELETE FROM mood"))


# ── row ⇄ dict projection (stable on-disk shape) ─────────────────────────────


def _episode_dict(r: EpisodeRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "ts": r.ts.isoformat(),
        "kind": r.kind,
        "content": r.content,
        "actors": list(r.actors),
        "emotion_tags": list(r.emotion_tags),
        "salience": r.salience,
        "embedding": [float(x) for x in r.embedding],
    }


def _episode_row(d: dict[str, Any]) -> dict[str, Any]:
    return {**d, "ts": datetime.fromisoformat(d["ts"])}


def _fact_dict(r: SemanticFactRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "subject": r.subject,
        "predicate": r.predicate,
        "object": r.object,
        "confidence": r.confidence,
        "source_episode_ids": list(r.source_episode_ids),
        "embedding": [float(x) for x in r.embedding],
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }


def _fact_row(d: dict[str, Any]) -> dict[str, Any]:
    return {
        **d,
        "created_at": datetime.fromisoformat(d["created_at"]),
        "updated_at": datetime.fromisoformat(d["updated_at"]),
    }


def _edge_dict(r: SemanticEdgeRow) -> dict[str, Any]:
    return {"id": r.id, "from_fact": r.from_fact, "to_fact": r.to_fact, "relation": r.relation}


def _skill_dict(r: SkillRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "recipe": dict(r.recipe),
        "success_rate": r.success_rate,
        "uses": r.uses,
        "successes": r.successes,
        "embedding": [float(x) for x in r.embedding],
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }


def _skill_row(d: dict[str, Any]) -> dict[str, Any]:
    return {
        **d,
        "created_at": datetime.fromisoformat(d["created_at"]),
        "updated_at": datetime.fromisoformat(d["updated_at"]),
    }


def _identity_dict(r: IdentityRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "self_model_doc": r.self_model_doc,
        "values": list(r.values),
        "concerns": list(r.concerns),
        "relationships": dict(r.relationships),
        "version": r.version,
        "created_at": r.created_at.isoformat(),
    }


def _identity_row(d: dict[str, Any]) -> dict[str, Any]:
    return {**d, "created_at": datetime.fromisoformat(d["created_at"])}


def _drive_dict(r: DriveStateRow) -> dict[str, Any]:
    return {
        "drive": r.drive,
        "value": r.value,
        "setpoint": r.setpoint,
        "accrual_rate": r.accrual_rate,
        "decay_rate": r.decay_rate,
        "threshold": r.threshold,
        "updated_at": r.updated_at.isoformat(),
    }


def _drive_row(d: dict[str, Any]) -> dict[str, Any]:
    return {**d, "updated_at": datetime.fromisoformat(d["updated_at"])}


def _mood_dict(r: MoodRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "ts": r.ts.isoformat(),
        "valence": r.valence,
        "arousal": r.arousal,
        "emotions": dict(r.emotions),
    }


def _mood_row(d: dict[str, Any]) -> dict[str, Any]:
    return {**d, "ts": datetime.fromisoformat(d["ts"])}


def _goal_dict(r: GoalRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "source": r.source,
        "description": r.description,
        "priority": r.priority,
        "status": r.status,
        "plan": dict(r.plan),
        "outcome": dict(r.outcome),
        "created_at": r.created_at.isoformat(),
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at is not None else None,
    }


def _goal_row(d: dict[str, Any]) -> dict[str, Any]:
    resolved = d.get("resolved_at")
    return {
        **d,
        "created_at": datetime.fromisoformat(d["created_at"]),
        "resolved_at": datetime.fromisoformat(resolved) if resolved is not None else None,
    }


# ── jsonl + sequence helpers ─────────────────────────────────────────────────


def _write_jsonl(path: Path, rows: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row))
            fh.write("\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


async def _resync_sequences(session: AsyncSession, *, restore_v2: bool) -> None:
    """Set each serial table's id sequence past the restored max so new inserts don't clash.

    The 3-arg ``setval`` form sets ``is_called`` from whether the table has rows:
    a populated table resumes at ``max(id)+1``; an empty one next-produces ``1``.
    ``drive_state`` is keyed by ``drive`` (no serial) so it is never resynced.
    """
    tables = _SERIAL_TABLES if restore_v2 else _MEMORY_TABLES
    for table in tables:
        await session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1), "
                f"(SELECT COUNT(*) FROM {table}) > 0)"
            )
        )
