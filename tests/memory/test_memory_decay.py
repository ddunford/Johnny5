"""Memory decay + merge: decay ≠ deletion, facts dedupe (TC-4.2, TASK-4.10 slice).

The forgetting/consolidating half of sleep (``SPEC §8``). Two invariants this suite
exists to defend:

* **Decay ≠ deletion (the headline).** An episodic decay pass only *lowers*
  ``salience`` (toward a floor) on age — it must **never delete an episode row**.
  Episodic memory is append-only in v1; the autobiography has to survive. So the
  load-bearing assertion is *row count unchanged* across a decay run, salience down.
  Emotionally-charged (emotion-tagged) or goal-relevant (consolidated-source)
  episodes are *strengthened* instead, resisting the fade.
* **Only semantic facts merge.** Near-duplicate facts (cosine ≥ threshold) collapse
  into the earliest, unioning provenance and taking max confidence; episodes never
  merge.

``adjusted_salience`` and ``duplicate_fact_groups`` are pure (host-runnable) — the
deterministic core. ``MemoryDecay.run()`` tests are DB-backed (rows persist + the
merge deletes fact rows) → run in-network via ``./ctl.sh test``. ``now`` is injected
so age-based decay is exact, never wall-clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from helpers.embeddings import axis_vector, perturbed
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.memory.decay import MemoryDecay, adjusted_salience, duplicate_fact_groups
from brain.memory.episodic import EpisodeRow
from brain.memory.semantic import SemanticFactRow
from foundation.db import session_scope

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_HALFLIFE = 100.0  # seconds — short, so a test ages an episode across it cheaply
_FLOOR = 0.05
_BOOST = 0.25


# ── adjusted_salience: the pure age→salience rule ────────────────────────────────


def test_uncharged_salience_decays_toward_floor_over_a_halflife() -> None:
    """One half-life of age retains half the above-floor salience — down, not gone."""
    new = adjusted_salience(
        0.8,
        _T0,
        now=_T0 + timedelta(seconds=_HALFLIFE),
        halflife_seconds=_HALFLIFE,
        floor=_FLOOR,
        charged=False,
        charged_boost=_BOOST,
    )
    # floor + (0.8 - floor) * 0.5
    assert new == pytest.approx(_FLOOR + (0.8 - _FLOOR) * 0.5)
    assert _FLOOR < new < 0.8


def test_very_old_uncharged_salience_approaches_but_never_drops_below_floor() -> None:
    """A long-dead memory fades to the floor and is clamped there — never below 0."""
    new = adjusted_salience(
        0.9,
        _T0,
        now=_T0 + timedelta(seconds=_HALFLIFE * 50),
        halflife_seconds=_HALFLIFE,
        floor=_FLOOR,
        charged=False,
        charged_boost=_BOOST,
    )
    assert new == pytest.approx(_FLOOR, abs=1e-6)
    assert new >= _FLOOR


def test_charged_episode_is_strengthened_above_its_current_salience() -> None:
    """A charged (emotion-tagged / goal-relevant) memory is pulled toward 1.0 — it
    strengthens rather than fades, even with no age."""
    new = adjusted_salience(
        0.4,
        _T0,
        now=_T0,  # age 0: uncharged would be unchanged at 0.4
        halflife_seconds=_HALFLIFE,
        floor=_FLOOR,
        charged=True,
        charged_boost=_BOOST,
    )
    # 0.4 + 0.25 * (1 - 0.4)
    assert new == pytest.approx(0.4 + _BOOST * (1.0 - 0.4))
    assert new > 0.4


# ── duplicate_fact_groups: the pure near-duplicate grouping ──────────────────────


def _fact_row(id_: int, embedding: Sequence[float]) -> SemanticFactRow:
    """A detached ``SemanticFactRow`` for the pure grouping test (embedding only)."""
    return SemanticFactRow(
        id=id_,
        subject=f"s{id_}",
        predicate="p",
        object=f"o{id_}",
        confidence=0.5,
        source_episode_ids=[],
        embedding=list(embedding),
    )


def test_near_duplicates_group_together_distinct_facts_do_not() -> None:
    """Two near-identical embeddings group; an orthogonal one stays its own group.
    The earliest (lowest id) is the group's representative/survivor."""
    base = axis_vector(0)
    facts = [
        _fact_row(1, base),
        _fact_row(2, perturbed(base, strength=0.02)),  # cosine ≈ 0.9998 ≥ 0.95
        _fact_row(3, axis_vector(1)),  # orthogonal — its own group
    ]

    groups = duplicate_fact_groups(facts, threshold=0.95)

    idsets = [[f.id for f in g] for g in groups]
    assert idsets == [[1, 2], [3]]


def test_facts_below_the_merge_threshold_stay_separate() -> None:
    """A fact that is *similar but not a restatement* (cosine < threshold) must not
    merge — the high threshold means only true duplicates collapse."""
    base = axis_vector(0)
    facts = [
        _fact_row(1, base),
        _fact_row(2, perturbed(base, strength=0.5)),  # cosine ≈ 0.89 < 0.95
    ]

    groups = duplicate_fact_groups(facts, threshold=0.95)

    assert [[f.id for f in g] for g in groups] == [[1], [2]]


# ── MemoryDecay.run(): DB-backed decay (never deletes) + merge ───────────────────


async def _insert_episode(
    *, salience: float, ts: datetime, emotion_tags: Sequence[str] = ()
) -> int:
    async with session_scope() as session:
        row = EpisodeRow(
            kind="observation",
            content="a thing happened",
            actors=[],
            emotion_tags=list(emotion_tags),
            salience=salience,
            ts=ts,
            embedding=axis_vector(0),
        )
        session.add(row)
        await session.flush()
        return row.id


async def _insert_fact(
    *,
    subject: str,
    predicate: str,
    obj: str,
    embedding: Sequence[float],
    confidence: float,
    sources: Sequence[int],
) -> int:
    async with session_scope() as session:
        row = SemanticFactRow(
            subject=subject,
            predicate=predicate,
            object=obj,
            confidence=confidence,
            source_episode_ids=list(sources),
            embedding=list(embedding),
        )
        session.add(row)
        await session.flush()
        return row.id


async def _episode_count() -> int:
    async with session_scope() as session:
        return (await session.execute(select(func.count()).select_from(EpisodeRow))).scalar_one()


async def _episode_salience(episode_id: int) -> float:
    async with session_scope() as session:
        return (
            await session.execute(select(EpisodeRow.salience).where(EpisodeRow.id == episode_id))
        ).scalar_one()


def _decay(**overrides: object) -> MemoryDecay:
    params: dict[str, object] = {
        "halflife_seconds": _HALFLIFE,
        "salience_floor": _FLOOR,
        "charged_boost": _BOOST,
    }
    params.update(overrides)
    return MemoryDecay(**params)  # type: ignore[arg-type]


async def test_decay_lowers_salience_but_never_deletes_a_row(memory_db: AsyncEngine) -> None:
    """The append-only invariant: an aged episode's salience drops, but the row
    survives — the episode COUNT is unchanged across the pass (SPEC §8)."""
    old_id = await _insert_episode(salience=0.8, ts=_T0)
    # A second old episode so the count assertion is meaningful (>1 row).
    await _insert_episode(salience=0.6, ts=_T0)

    count_before = await _episode_count()
    report = await _decay().run(now=_T0 + timedelta(seconds=_HALFLIFE * 3))
    count_after = await _episode_count()

    assert count_after == count_before == 2  # nothing deleted — decay ≠ deletion
    assert report.episodes_decayed == 2
    assert await _episode_salience(old_id) < 0.8  # salience fell
    assert await _episode_salience(old_id) >= _FLOOR  # but not below the floor


async def test_charged_episodes_are_strengthened_not_faded(memory_db: AsyncEngine) -> None:
    """Emotion-tagged and consolidated-source (goal-relevant) episodes strengthen;
    a plain aged episode in the same pass decays — and nothing is deleted."""
    emotional_id = await _insert_episode(salience=0.4, ts=_T0, emotion_tags=["frustration"])
    consolidated_id = await _insert_episode(salience=0.4, ts=_T0)  # referenced by a fact below
    plain_old_id = await _insert_episode(salience=0.8, ts=_T0)
    # Mark `consolidated_id` as goal-relevant: a semantic fact was distilled from it.
    await _insert_fact(
        subject="a lesson",
        predicate="came from",
        obj="that moment",
        embedding=axis_vector(5),
        confidence=0.6,
        sources=[consolidated_id],
    )

    report = await _decay().run(now=_T0 + timedelta(seconds=_HALFLIFE * 2))

    assert await _episode_count() == 3  # no episode deleted
    assert await _episode_salience(emotional_id) > 0.4  # charged → strengthened
    assert await _episode_salience(consolidated_id) > 0.4  # goal-relevant → strengthened
    assert await _episode_salience(plain_old_id) < 0.8  # uncharged + aged → decayed
    assert report.episodes_strengthened == 2
    assert report.episodes_decayed == 1


async def test_near_duplicate_facts_merge_and_episodes_never_merge(
    memory_db: AsyncEngine,
) -> None:
    """Two near-duplicate semantic facts collapse into the earliest — provenance
    unioned, confidence maxed — while a distinct fact and every episode survive."""
    # An episode so we can prove episode rows are untouched by a fact merge.
    await _insert_episode(salience=0.5, ts=_T0)

    base = axis_vector(0)
    survivor_id = await _insert_fact(
        subject="the rig",
        predicate="runs",
        obj="hot",
        embedding=base,
        confidence=0.6,
        sources=[1, 2],
    )
    dup_id = await _insert_fact(
        subject="the server",
        predicate="is",
        obj="overheating",
        embedding=perturbed(base, strength=0.02),  # cosine ≈ 0.9998 ≥ 0.95 → duplicate
        confidence=0.9,
        sources=[2, 3],
    )
    distinct_id = await _insert_fact(
        subject="a parcel",
        predicate="arrived",
        obj="today",
        embedding=axis_vector(1),  # orthogonal — not a duplicate
        confidence=0.5,
        sources=[4],
    )

    report = await _decay().run(now=_T0)

    assert report.facts_merged == 1
    assert await _episode_count() == 1  # episodes never merge — only facts

    async with session_scope() as session:
        remaining_ids = set((await session.execute(select(SemanticFactRow.id))).scalars().all())
        survivor = (
            await session.execute(select(SemanticFactRow).where(SemanticFactRow.id == survivor_id))
        ).scalar_one()

    assert dup_id not in remaining_ids  # the later duplicate was merged away
    assert {survivor_id, distinct_id} <= remaining_ids  # survivor + distinct fact remain
    assert set(survivor.source_episode_ids) == {1, 2, 3}  # provenance unioned
    assert survivor.confidence == pytest.approx(0.9)  # max confidence of the group
