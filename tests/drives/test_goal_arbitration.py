"""Goal arbitration: promote the winning urge, and don't thrash (TC-3.6, SPEC §6.1).

The arbiter turns the strongest actionable urge into a goal Johnny commits to.
Two rules carry the autonomy loop and are pinned here:

* **Affect-weighted priority** — priority = urge urgency × (1 + arousal weight), so
  an activated Johnny pursues his strongest need more decisively.
* **Anti-thrash hysteresis** — a just-promoted goal is held for a dwell window
  before *anything* can displace it, and after the dwell a different drive must
  beat the incumbent's current priority by a margin. So Johnny commits instead of
  flip-flopping between curiosity and connection every tick.

``select`` is the pure decision (no DB) — the bulk of the oracle. The headline
no-thrash test drives the DB-backed ``arbitrate`` across ticks and asserts the
pursuit is stable; it's DB-backed → run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.affect.appraisal import Mood
from brain.drives.engine import SLEEP_DRIVE, Urge
from brain.goals.arbiter import GoalArbiter
from brain.goals.store import STATUS_ACTIVE, Goal, GoalStore

# Defaults the assertions key off (config): dwell 45s, margin 0.15, arousal_wt 0.5.
_DWELL = 45.0
_MOOD = Mood(valence=0.0, arousal=0.5)  # priority factor = 1 + 0.5×0.5 = 1.25


def _urge(drive: str, urgency: float, *, threshold: float = 0.65) -> Urge:
    """An over-threshold urge with the given normalised urgency."""
    value = threshold + urgency * (1.0 - threshold)
    return Urge(drive=drive, value=value, threshold=threshold, urgency=urgency)


def _arbiter(now_fn) -> GoalArbiter:
    return GoalArbiter(store=GoalStore(now_fn=now_fn), now_fn=now_fn)


# ── the pure decision (select) ───────────────────────────────────────────────


def test_promotes_the_highest_priority_urge_when_idle() -> None:
    arbiter = GoalArbiter()
    t = datetime(2026, 1, 1, tzinfo=UTC)
    goal = arbiter.select([_urge("curiosity", 0.4), _urge("connection", 0.7)], _MOOD, [], now=t)
    assert goal is not None
    assert goal.source == "connection"  # higher urgency → higher priority


def test_sleep_urge_is_not_promoted_to_a_goal() -> None:
    """Energy over threshold is a request to sleep (Phase 4), not a pursuit."""
    arbiter = GoalArbiter()
    t = datetime(2026, 1, 1, tzinfo=UTC)
    assert arbiter.select([_urge(SLEEP_DRIVE, 0.9, threshold=0.80)], _MOOD, [], now=t) is None


def test_fresh_incumbent_is_held_through_the_dwell_even_against_a_stronger_urge() -> None:
    """Hysteresis 1 — minimum dwell: a just-promoted goal isn't displaced at all,
    even by a much stronger competing drive."""
    arbiter = GoalArbiter()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    incumbent = Goal(id=1, source="curiosity", description="explore", priority=0.5, created_at=t0)
    # A far stronger connection urge, but only 10s into the 45s dwell.
    decision = arbiter.select(
        [_urge("connection", 0.99)], _MOOD, [incumbent], now=t0 + timedelta(seconds=10)
    )
    assert decision is None  # held — no flip-flop


def test_after_dwell_a_marginal_challenger_does_not_switch_but_a_clear_one_does() -> None:
    """Hysteresis 2 — margin: past the dwell, a different drive must clearly beat the
    incumbent's current priority (by the margin) to take over."""
    arbiter = GoalArbiter()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    later = t0 + timedelta(seconds=_DWELL + 5)
    incumbent = Goal(id=1, source="curiosity", description="explore", priority=0.6, created_at=t0)

    # Curiosity still pressing (0.5) vs a slightly higher connection (0.58):
    # 0.58×1.25 = 0.725 does NOT beat 0.5×1.25 + 0.15 = 0.775 → no switch.
    marginal = arbiter.select(
        [_urge("curiosity", 0.5), _urge("connection", 0.58)], _MOOD, [incumbent], now=later
    )
    assert marginal is None

    # A clearly stronger connection (0.8 → 1.0) beats 0.775 → switch.
    decisive = arbiter.select(
        [_urge("curiosity", 0.5), _urge("connection", 0.8)], _MOOD, [incumbent], now=later
    )
    assert decisive is not None
    assert decisive.source == "connection"


def test_same_drive_still_winning_keeps_the_pursuit() -> None:
    """When the incumbent's own drive is still the strongest, nothing is re-promoted
    — Johnny just keeps pursuing it (no churn)."""
    arbiter = GoalArbiter()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    later = t0 + timedelta(seconds=_DWELL + 5)
    incumbent = Goal(id=1, source="curiosity", description="explore", priority=0.6, created_at=t0)
    assert (
        arbiter.select(
            [_urge("curiosity", 0.9), _urge("connection", 0.3)], _MOOD, [incumbent], now=later
        )
        is None
    )


# ── the headline: arbitration does not thrash (DB-backed) ────────────────────


async def test_arbitration_does_not_thrash_between_competing_drives(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """Two drives over threshold near-simultaneously: one goal is promoted and held
    for a stable stretch (the dwell), with no per-tick flip-flop even as the
    competitor edges ahead — then, past the dwell, a clear winner does take over."""
    now_fn = datetime_from(frozen_clock)
    arbiter = _arbiter(now_fn)

    # Curiosity edges out connection first → it's promoted.
    first = await arbiter.arbitrate([_urge("curiosity", 0.60), _urge("connection", 0.55)], _MOOD)
    assert first is not None and first.source == "curiosity"
    goal_id = first.id

    # Over the next several ticks (each well under the 45s dwell) connection is now
    # clearly stronger — but the incumbent is held: no thrash.
    for _ in range(5):
        frozen_clock.advance(5)
        pursued = await arbiter.arbitrate(
            [_urge("curiosity", 0.50), _urge("connection", 0.95)], _MOOD
        )
        assert pursued is not None
        assert pursued.id == goal_id
        assert pursued.source == "curiosity"

    # Exactly one active goal exists — no promote/abandon churn during the hold.
    active = await GoalStore(now_fn=now_fn).active()
    assert [g.id for g in active] == [goal_id]

    # Past the dwell, the clearly stronger connection finally takes over.
    frozen_clock.advance(_DWELL)
    switched = await arbiter.arbitrate([_urge("curiosity", 0.40), _urge("connection", 0.95)], _MOOD)
    assert switched is not None
    assert switched.source == "connection"
    assert switched.id != goal_id
    # And only the new pursuit is active (the old one was abandoned, not duplicated).
    active = await GoalStore(now_fn=now_fn).active()
    assert [g.source for g in active] == ["connection"]
    assert all(g.status == STATUS_ACTIVE for g in active)
