"""Memory snapshot / restore — the continuity primitive (FC-6).

Johnny's continuity (the safety backups *and* the Continuity drive) depends on
memory surviving restarts and being restorable into a clean database. This ships
the snapshot/restore primitive in Phase 1; Phase 4 schedules it as part of sleep.

A snapshot is a directory under the gitignored ``snapshots/`` tree:

    <root>/<label>/
        manifest.json          # version, created_at, per-store counts
        episodes.jsonl
        semantic_facts.jsonl
        semantic_edges.jsonl
        skills.jsonl
        working_memory.jsonl

The on-disk format is **stable** (versioned by ``SNAPSHOT_VERSION``) — keep it
backward-compatible so an old backup always restores. Embeddings are stored as
plain float lists; primary keys are preserved so a restore reproduces the store
identically (sequences are re-synced afterwards). Restore is destructive by
default (``replace=True`` truncates first) so it is idempotent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from brain.memory.episodic import EpisodeRow
from brain.memory.procedural import SkillRow
from brain.memory.semantic import SemanticEdgeRow, SemanticFactRow
from brain.memory.working import WorkingMemory, WorkingMemoryItem
from foundation.config import get_settings
from foundation.db import session_scope
from foundation.observability import get_logger

_log = get_logger("brain.memory.snapshot")

SNAPSHOT_VERSION = 1

_EPISODES = "episodes.jsonl"
_FACTS = "semantic_facts.jsonl"
_EDGES = "semantic_edges.jsonl"
_SKILLS = "skills.jsonl"
_WORKING = "working_memory.jsonl"
_MANIFEST = "manifest.json"

# Postgres tables in FK-safe restore order (facts before the edges that reference them).
_TABLES = ("episode", "semantic_fact", "semantic_edge", "skill")


class MemorySnapshot:
    """Dump every memory store to disk and reload it.

    ``root`` defaults to ``Settings.memory_snapshot_dir`` (gitignored). ``working``
    is injectable so the same Redis instance/namespace is snapshotted as used live.
    """

    def __init__(self, *, root: Path | None = None, working: WorkingMemory | None = None) -> None:
        settings = get_settings()
        self._root = root or Path(settings.memory_snapshot_dir)
        self._working = working or WorkingMemory()

    async def snapshot(self, *, label: str | None = None) -> Path:
        """Write a full snapshot and return its directory."""
        stamp = label or datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = self._root / stamp
        target.mkdir(parents=True, exist_ok=True)

        async with session_scope() as session:
            episodes = list((await session.execute(select(EpisodeRow))).scalars().all())
            facts = list((await session.execute(select(SemanticFactRow))).scalars().all())
            edges = list((await session.execute(select(SemanticEdgeRow))).scalars().all())
            skills = list((await session.execute(select(SkillRow))).scalars().all())

        _write_jsonl(target / _EPISODES, (_episode_dict(r) for r in episodes))
        _write_jsonl(target / _FACTS, (_fact_dict(r) for r in facts))
        _write_jsonl(target / _EDGES, (_edge_dict(r) for r in edges))
        _write_jsonl(target / _SKILLS, (_skill_dict(r) for r in skills))

        working_items = await self._working.export_items()
        _write_jsonl(target / _WORKING, (i.model_dump() for i in working_items))

        manifest = {
            "version": SNAPSHOT_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "counts": {
                "episodes": len(episodes),
                "semantic_facts": len(facts),
                "semantic_edges": len(edges),
                "skills": len(skills),
                "working_memory": len(working_items),
            },
        }
        (target / _MANIFEST).write_text(json.dumps(manifest, indent=2))
        _log.info("memory.snapshot.written", path=str(target), counts=manifest["counts"])
        return target

    async def restore(self, path: str | Path, *, replace: bool = True) -> dict[str, int]:
        """Load a snapshot back into the stores; return per-store row counts.

        With ``replace`` (default) the persistent tables and the working set are
        truncated first, so restoring into a clean DB reproduces it exactly and
        re-restoring is idempotent. Primary keys are preserved; sequences are
        re-synced so subsequent inserts don't collide.
        """
        target = Path(path)
        manifest = json.loads((target / _MANIFEST).read_text())
        if manifest.get("version") != SNAPSHOT_VERSION:
            raise ValueError(
                f"unsupported snapshot version {manifest.get('version')!r} "
                f"(this build reads v{SNAPSHOT_VERSION})"
            )

        episodes = _read_jsonl(target / _EPISODES)
        facts = _read_jsonl(target / _FACTS)
        edges = _read_jsonl(target / _EDGES)
        skills = _read_jsonl(target / _SKILLS)
        working_items = _read_jsonl(target / _WORKING)

        async with session_scope() as session:
            if replace:
                for table in reversed(_TABLES):
                    await session.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))
            session.add_all(EpisodeRow(**_episode_row(d)) for d in episodes)
            session.add_all(SemanticFactRow(**_fact_row(d)) for d in facts)
            await session.flush()  # facts must exist before edges (FK)
            session.add_all(SemanticEdgeRow(**d) for d in edges)
            session.add_all(SkillRow(**_skill_row(d)) for d in skills)
            await session.flush()
            await _resync_sequences(session)

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
        _log.info("memory.snapshot.restored", path=str(target), counts=counts)
        return counts


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


async def _resync_sequences(session: Any) -> None:
    """Set each table's id sequence past the restored max so new inserts don't clash.

    The 3-arg ``setval`` form sets ``is_called`` from whether the table has rows:
    a populated table resumes at ``max(id)+1``; an empty one next-produces ``1``.
    """
    for table in _TABLES:
        await session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1), "
                f"(SELECT COUNT(*) FROM {table}) > 0)"
            )
        )
