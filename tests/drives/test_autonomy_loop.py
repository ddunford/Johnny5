"""The autonomy loop closes with zero input (TC-3.2 — the headline, SPEC §6).

This is the proof that Johnny *wants*: left completely alone, his drives accrue
over time until one crosses threshold, an urge is emitted, the arbiter promotes it
to a goal, and Deliberation chooses an internal action to pursue it — then acting
eases the drive that spawned it and the goal resolves. No input at any step.

The whole chain is exercised against the REAL DriveEngine + GoalArbiter +
Deliberation, made deterministic by:

* a frozen clock fast-forwarding the idle accrual (no wall time),
* ``router=None`` so Deliberation takes its templated fallback (no LLM — the loop
  still closes when every provider is tired, by design),
* a ``DeterministicEmbedder`` for the episodic trace (no embedding network call).

DB-backed (drives/goals/episodes persist) → run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from helpers.embeddings import DeterministicEmbedder
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.affect.appraisal import Mood
from brain.agents.deliberation import ACTION_REFLECT, Deliberation
from brain.drives.engine import SLEEP_DRIVE, DriveEngine
from brain.goals.store import GoalStore
from brain.memory.episodic import EpisodicMemory

_IDLE_MOOD = Mood(valence=0.0, arousal=0.4)


def _deliberation(now_fn) -> Deliberation:
    """Real Deliberation wired to its own store/arbiter, offline-deterministic:
    no router (templated reflection) and a deterministic embedder for the trace."""
    return Deliberation(
        router=None,
        episodic=EpisodicMemory(DeterministicEmbedder()),
        now_fn=now_fn,
    )


async def test_idle_accrual_spawns_a_goal_and_an_internal_action_with_no_input(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    now_fn = datetime_from(frozen_clock)
    drives = DriveEngine(now_fn=now_fn)
    await drives.bootstrap()
    deliberation = _deliberation(now_fn)

    # ── zero input — only time passes ──
    frozen_clock.advance(3600)  # an hour idle
    readings = await drives.step()  # advance + persist; no events
    urges = DriveEngine.urges(readings)

    # An urge emerged from idle accrual alone — Curiosity is the strongest.
    assert any(u.drive == "curiosity" for u in urges)
    assert all(not u.is_sleep_signal or u.drive == SLEEP_DRIVE for u in urges)

    # Arbiter promotes the winning urge → Deliberation plans an internal action.
    result = await deliberation.deliberate(urges=urges, mood=_IDLE_MOOD, contents=[])

    assert result.goal is not None
    assert result.goal.source == "curiosity"  # the strongest idle drive won
    assert result.goal.id is not None  # persisted (resumes across restart)
    assert result.action is not None
    assert result.action.kind == ACTION_REFLECT  # curiosity → reflect (internal)
    assert result.action.goal_id == result.goal.id

    # The goal is the single active pursuit — and it's a drive goal, not the
    # Energy sleep signal (that's never promoted).
    active = await GoalStore(now_fn=now_fn).active()
    assert [g.source for g in active] == ["curiosity"]


async def test_acting_on_the_goal_eases_its_drive_and_resolves_it(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """The loop closes: executing the internal action emits a satisfaction event
    that, fed back to the drives, lowers the drive that spawned the goal — and the
    goal is resolved, not left pursued forever."""
    now_fn = datetime_from(frozen_clock)
    drives = DriveEngine(now_fn=now_fn)
    await drives.bootstrap()
    deliberation = _deliberation(now_fn)

    frozen_clock.advance(3600)
    readings = await drives.step()
    curiosity_peak = next(r.value for r in readings if r.drive == "curiosity")
    assert curiosity_peak > 0.65

    result = await deliberation.deliberate(
        urges=DriveEngine.urges(readings), mood=_IDLE_MOOD, contents=[]
    )
    assert result.goal is not None and result.action is not None

    # Execute the action → satisfaction event(s) feed back into the drives.
    outcome = await deliberation.act(result.action, result.goal, contents=[])
    assert outcome.success
    assert outcome.drive_events, "acting should produce a satisfaction event"

    frozen_clock.advance(10)
    after = {r.drive: r for r in await drives.step(outcome.drive_events)}
    assert after["curiosity"].value < curiosity_peak  # the need was eased by acting
    assert after["curiosity"].value < 0.65  # pulled back under threshold

    # The acted-on goal is resolved (not still active), so it can't re-trigger.
    active = await GoalStore(now_fn=now_fn).active()
    assert result.goal.id not in {g.id for g in active}


async def test_deliberation_acts_at_most_once_per_cadence_even_with_a_pegged_drive(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """The 3.12 anti-spin guarantee: even with a drive pegged over threshold, the
    goal→action loop is bounded — Deliberation *acts* at most once per its cadence,
    so a stuck need can't spin the (LLM-bearing) action loop every tick."""
    now_fn = datetime_from(frozen_clock)
    drives = DriveEngine(now_fn=now_fn)
    await drives.bootstrap()
    deliberation = Deliberation(
        router=None,
        episodic=EpisodicMemory(DeterministicEmbedder()),
        min_interval_seconds=20.0,
        now_fn=now_fn,
    )

    frozen_clock.advance(3600)  # curiosity pegged well over threshold and stays there
    first = await deliberation.deliberate(
        urges=DriveEngine.urges(await drives.step()), mood=_IDLE_MOOD, contents=[]
    )
    assert first.action is not None
    await deliberation.act(first.action, first.goal, contents=[])  # stamps the cadence

    # The drive is still pegged so a fresh goal is promoted — but within the cadence
    # window Deliberation does NOT act again.
    frozen_clock.advance(5)  # < 20s
    held = await deliberation.deliberate(
        urges=DriveEngine.urges(await drives.step()), mood=_IDLE_MOOD, contents=[]
    )
    assert held.goal is not None  # still pursuing
    assert held.action is None  # but not acting — the loop is bounded

    # Once the cadence elapses, it acts again.
    frozen_clock.advance(20)  # now ≥ 20s since the last action
    resumed = await deliberation.deliberate(
        urges=DriveEngine.urges(await drives.step()), mood=_IDLE_MOOD, contents=[]
    )
    assert resumed.action is not None
