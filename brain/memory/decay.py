"""Memory decay + merge — the forgetting/consolidating half of sleep (``SPEC §8``).

Two offline passes that run alongside consolidation while Johnny sleeps:

* **Episodic decay** — lower the ``salience`` of episodes on age, so recall
  favours what still matters. Crucially **decay ≠ deletion**: episodic memory is
  *append-only* in v1 (``SPEC §8``) — an episode row is never hard-deleted, only
  its salience falls toward a floor. Emotionally-charged or goal-relevant memories
  (those tagged with emotions, or already distilled into a semantic fact) are
  *strengthened* instead, resisting the fade — the autobiography keeps what counts
  vivid and lets the trivia recede.

* **Semantic merge** — dedupe near-duplicate semantic *facts* (cosine over the
  existing 1024-d embeddings). Unlike episodes, consolidated facts *may* merge:
  near-identical restatements collapse into one, unioning their source-episode
  provenance and taking the max confidence, so the knowledge graph stays clean as
  consolidation re-asserts overlapping facts across sleeps.

The salience update and the duplicate grouping are **pure** functions
(``adjusted_salience`` / ``duplicate_fact_groups``) so a test can assert
"salience went down, the row still exists" and "these two facts merged, that one
didn't" deterministically with an injected clock + threshold — the same freezing
seam as the drive engine and the consolidator.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from brain.memory.base import clamp01, cosine_similarity, utcnow
from brain.memory.episodic import EpisodeRow
from brain.memory.semantic import SemanticEdgeRow, SemanticFactRow
from foundation.config import get_settings
from foundation.db import session_scope
from foundation.observability import get_logger

_log = get_logger("brain.memory.decay")


class DecayReport(BaseModel):
    """What one decay+merge pass did — feeds ``sleep_log`` + the REPL summary."""

    episodes_decayed: int = 0
    episodes_strengthened: int = 0
    facts_merged: int = 0


# ── pure projections (deterministic, for the tests) ────────────────────────────


def adjusted_salience(
    salience: float,
    ts: datetime,
    *,
    now: datetime,
    halflife_seconds: float,
    floor: float,
    charged: bool,
    charged_boost: float,
) -> float:
    """The new salience for an episode given its age (pure).

    Uncharged: salience relaxes toward ``floor`` with the configured half-life — a
    fresh episode is ~unchanged, an old one approaches the floor (but never below,
    and the row is never removed). Charged (emotion-tagged or goal-relevant): after
    decay it is pulled ``charged_boost`` of the way toward 1.0, so it *strengthens*
    rather than fades. Result is clamped to ``[0, 1]``.
    """
    age = max(0.0, (now - ts).total_seconds())
    retained = 0.5 ** (age / halflife_seconds) if halflife_seconds > 0 else 1.0
    value = floor + (salience - floor) * retained
    if charged:
        value = value + charged_boost * (1.0 - value)
    return clamp01(value)


def duplicate_fact_groups(
    facts: Sequence[SemanticFactRow], *, threshold: float
) -> list[list[SemanticFactRow]]:
    """Group facts whose embeddings are near-duplicates (cosine ≥ ``threshold``).

    Greedy single-link by representative (the first/earliest member): each fact
    joins the first group it is similar enough to, else seeds its own. Pure given
    the embeddings, so grouping is deterministic. Process ``facts`` in a stable
    order (id) so the earliest fact is each group's representative/survivor.
    """
    groups: list[list[SemanticFactRow]] = []
    for fact in facts:
        vector = [float(x) for x in fact.embedding]
        for group in groups:
            representative = [float(x) for x in group[0].embedding]
            if cosine_similarity(vector, representative) >= threshold:
                group.append(fact)
                break
        else:
            groups.append([fact])
    return groups


# ── the decay/merge pass ───────────────────────────────────────────────────────


class MemoryDecay:
    """Lower episodic salience on age (never delete) and merge duplicate facts.

    Callable, not scheduled — the sleep cycle drives it. ``now`` comes from an
    injected clock so the age-based decay is freezable in tests.
    """

    def __init__(
        self,
        *,
        episode_limit: int | None = None,
        halflife_seconds: float | None = None,
        salience_floor: float | None = None,
        charged_boost: float | None = None,
        merge_threshold: float | None = None,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        settings = get_settings()
        self._episode_limit = (
            episode_limit if episode_limit is not None else settings.decay_episode_limit
        )
        self._halflife = (
            halflife_seconds
            if halflife_seconds is not None
            else settings.decay_salience_halflife_seconds
        )
        self._floor = (
            salience_floor if salience_floor is not None else settings.decay_salience_floor
        )
        self._charged_boost = (
            charged_boost if charged_boost is not None else settings.decay_charged_boost
        )
        self._merge_threshold = (
            merge_threshold if merge_threshold is not None else settings.semantic_merge_threshold
        )
        self._now_fn = now_fn

    async def run(self, *, now: datetime | None = None) -> DecayReport:
        """Run both passes in one transaction and report the counts."""
        reference = now if now is not None else self._now_fn()
        async with session_scope() as session:
            decayed, strengthened = await self._decay_episodes(session, reference)
            merged = await self._merge_facts(session)
        _log.info(
            "decay.run.done",
            episodes_decayed=decayed,
            episodes_strengthened=strengthened,
            facts_merged=merged,
        )
        return DecayReport(
            episodes_decayed=decayed,
            episodes_strengthened=strengthened,
            facts_merged=merged,
        )

    # ── episodic decay (never deletes) ─────────────────────────────────────────

    async def _decay_episodes(self, session: AsyncSession, now: datetime) -> tuple[int, int]:
        """Re-score episode salience by age; strengthen charged ones. No deletes."""
        charged_ids = await self._consolidated_source_ids(session)
        rows = (
            (
                await session.execute(
                    select(EpisodeRow).order_by(EpisodeRow.ts).limit(self._episode_limit)
                )
            )
            .scalars()
            .all()
        )
        decayed = strengthened = 0
        for row in rows:
            charged = bool(row.emotion_tags) or row.id in charged_ids
            new = adjusted_salience(
                row.salience,
                row.ts,
                now=now,
                halflife_seconds=self._halflife,
                floor=self._floor,
                charged=charged,
                charged_boost=self._charged_boost,
            )
            if new < row.salience - 1e-9:
                row.salience = new
                decayed += 1
            elif new > row.salience + 1e-9:
                row.salience = new
                strengthened += 1
        return decayed, strengthened

    @staticmethod
    async def _consolidated_source_ids(session: AsyncSession) -> set[int]:
        """Episode ids that a semantic fact was distilled from (the goal-relevant set).

        Being consolidated into knowledge is the signal that an episode mattered —
        those are strengthened alongside the emotion-tagged ones.
        """
        result = await session.execute(
            text("SELECT DISTINCT unnest(source_episode_ids) FROM semantic_fact")
        )
        return {int(value) for (value,) in result.all() if value is not None}

    # ── semantic merge (facts may dedupe; episodes may not) ─────────────────────

    async def _merge_facts(self, session: AsyncSession) -> int:
        """Merge near-duplicate facts into the earliest of each group; return #merged."""
        facts = list(
            (await session.execute(select(SemanticFactRow).order_by(SemanticFactRow.id)))
            .scalars()
            .all()
        )
        merged = 0
        for group in duplicate_fact_groups(facts, threshold=self._merge_threshold):
            survivor, *dups = group
            if not dups:
                continue
            provenance: set[int] = set(survivor.source_episode_ids)
            confidence = survivor.confidence
            for dup in dups:
                provenance |= set(dup.source_episode_ids)
                confidence = max(confidence, dup.confidence)
            survivor.source_episode_ids = sorted(provenance)
            survivor.confidence = confidence

            await self._repoint_edges(session, {d.id for d in dups}, survivor.id)
            await session.flush()
            for dup in dups:
                await session.delete(dup)
            merged += len(dups)
        return merged

    @staticmethod
    async def _repoint_edges(session: AsyncSession, dup_ids: set[int], survivor_id: int) -> None:
        """Re-aim edges off merged-away facts onto the survivor; drop loops/dupes.

        Edges referencing a duplicate fact are re-pointed to the survivor. An edge
        that becomes a self-loop, or that would collide with an existing
        ``(from, to, relation)`` edge, is deleted instead (the unique constraint).
        Nothing writes edges in v1, so this is usually a no-op — but it stays
        correct if the light knowledge graph is populated.
        """
        all_edges = list((await session.execute(select(SemanticEdgeRow))).scalars().all())
        existing_keys: set[tuple[int, int, str]] = set()
        affected: list[SemanticEdgeRow] = []
        for edge in all_edges:
            if edge.from_fact in dup_ids or edge.to_fact in dup_ids:
                affected.append(edge)
            else:
                existing_keys.add((edge.from_fact, edge.to_fact, edge.relation))

        for edge in affected:
            new_from = survivor_id if edge.from_fact in dup_ids else edge.from_fact
            new_to = survivor_id if edge.to_fact in dup_ids else edge.to_fact
            key = (new_from, new_to, edge.relation)
            if new_from == new_to or key in existing_keys:
                await session.delete(edge)
                continue
            edge.from_fact = new_from
            edge.to_fact = new_to
            existing_keys.add(key)
