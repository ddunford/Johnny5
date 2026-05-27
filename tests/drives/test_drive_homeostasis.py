"""The Drive engine's rate-based homeostasis — the source of autonomy (SPEC §6.1).

These prove the mechanism that makes Johnny *want* with no input at all:

* **TC-3.1** — left idle, the climbing drives (curiosity, boredom, connection,
  energy) accrue *monotonically* over time toward their equilibrium and cross
  their thresholds; the grounded drives (mastery, coherence, continuity) sit
  below threshold and never fire on idle alone (continuity = "will to live, kept
  grounded").
* **TC-3.3** — a satisfaction event pulls the right drive back down: a *learning*
  eases curiosity, an *interaction* eases connection (and boredom). Intensity
  scales the relief.
* **TC-3.7** — sustained activity drives energy over threshold, emitting a *sleep*
  signal (``Urge.is_sleep_signal``) rather than a goal to pursue; rest restores it.

Determinism comes from a frozen clock: ``DriveEngine`` takes an injected
``now_fn`` returning a tz-aware datetime, and ``advance`` fast-forwards the elapsed
time the engine accrues against (the same seam the circuit-breaker tests freeze).
Reuses the shared ``FrozenClock`` + ``datetime_from`` harness — no wall clock, no
network. DB-backed (``drives_db``), so run in-network via ``./ctl.sh test``.

The configured equilibria (``config/drives.toml``) the assertions key off:
curiosity≈0.82>0.65, boredom≈0.715>0.70, connection≈0.85>0.70, energy≈0.871>0.80
(fire on idle); mastery≈0.66<0.75, coherence≈0.55<0.75, continuity≈0.325<0.85
(grounded).
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.drives import DRIVE_NAMES, SLEEP_DRIVE, DriveEngine, DriveEvent, DriveReading
from brain.drives.engine import EVENT_INTERACTION, EVENT_LEARNING, EVENT_REST
from brain.drives.parameters import (
    BOREDOM,
    COHERENCE,
    CONNECTION,
    CONTINUITY,
    CURIOSITY,
    ENERGY,
    MASTERY,
)

# The drives that climb past threshold on idle alone vs the grounded ones that
# only fire when events push them over (continuity is the "will to live").
_IDLE_FIRING = {CURIOSITY, BOREDOM, CONNECTION, ENERGY}
_GROUNDED = {MASTERY, COHERENCE, CONTINUITY}


def _by_drive(readings: list[DriveReading]) -> dict[str, DriveReading]:
    """Index a step's readings by drive name for direct assertions."""
    return {r.drive: r for r in readings}


# ── TC-3.1: drives accumulate monotonically while idle ───────────────────────


async def test_climbing_drives_accumulate_monotonically_while_idle(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """With zero input, curiosity and boredom rise strictly each tick toward their
    threshold — rate-based build-up is what produces the "need input" beat."""
    engine = DriveEngine(now_fn=datetime_from(frozen_clock))
    await engine.bootstrap()

    curiosity: list[float] = []
    boredom: list[float] = []
    for _ in range(12):
        frozen_clock.advance(400)  # 12 × 400s = 80 min of idle time
        readings = _by_drive(await engine.step())
        curiosity.append(readings[CURIOSITY].value)
        boredom.append(readings[BOREDOM].value)

    # Strictly increasing — pressure only builds while idle (never dips).
    assert all(b > a for a, b in pairwise(curiosity))
    assert all(b > a for a, b in pairwise(boredom))
    # ... and it climbs across the urgency threshold.
    assert curiosity[-1] > 0.65
    assert boredom[-1] > 0.70


async def test_only_idle_climbing_drives_fire_continuity_stays_grounded(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """After a long idle stretch, exactly the climbing drives are over threshold;
    mastery/coherence/continuity stay grounded (their equilibrium sits below
    threshold, so idle alone never fires them — continuity isn't theatrical)."""
    engine = DriveEngine(now_fn=datetime_from(frozen_clock))
    await engine.bootstrap()

    frozen_clock.advance(4800)  # 80 min idle: every climbing drive has crossed
    readings = _by_drive(await engine.step())

    fired = {name for name in DRIVE_NAMES if readings[name].over_threshold}
    assert fired == _IDLE_FIRING
    # The grounded drives are present but below threshold.
    assert all(not readings[name].over_threshold for name in _GROUNDED)
    # Continuity in particular barely moved off its low setpoint.
    assert readings[CONTINUITY].value < 0.5


async def test_persisted_state_matches_the_last_step_without_advancing(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """``current()`` reads ``drive_state`` without advancing — the values it returns
    equal the last step's, proving the engine persisted them (TC-3.1 tail)."""
    engine = DriveEngine(now_fn=datetime_from(frozen_clock))
    await engine.bootstrap()
    frozen_clock.advance(3600)
    stepped = _by_drive(await engine.step())

    persisted = _by_drive(await engine.current())

    for name in DRIVE_NAMES:
        assert persisted[name].value == pytest.approx(stepped[name].value)


# ── urge emission (pure projection over readings) ────────────────────────────


def test_urges_is_pure_filters_under_threshold_and_orders_by_urgency() -> None:
    """``DriveEngine.urges`` is a pure staticmethod (no DB): it keeps only the
    over-threshold readings and orders them most-urgent first."""
    readings = [
        DriveReading(
            drive=CURIOSITY,
            value=0.70,
            setpoint=0.10,
            accrual_rate=0.0008,
            decay_rate=0.0002,
            threshold=0.65,
        ),
        DriveReading(
            drive=CONNECTION,
            value=0.90,
            setpoint=0.10,
            accrual_rate=0.0005,
            decay_rate=0.0001,
            threshold=0.70,
        ),
        DriveReading(
            drive=MASTERY,
            value=0.50,  # under threshold → excluded
            setpoint=0.15,
            accrual_rate=0.0003,
            decay_rate=0.0002,
            threshold=0.75,
        ),
    ]

    urges = DriveEngine.urges(readings)

    # Mastery (under threshold) is filtered out; connection's larger overshoot
    # makes it more urgent, so it sorts ahead of curiosity.
    assert [u.drive for u in urges] == [CONNECTION, CURIOSITY]
    assert urges[0].urgency >= urges[1].urgency


async def test_idle_urges_cover_the_climbing_drives_most_urgent_first(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """The urges emitted from a long idle stretch are exactly the climbing drives,
    ordered by urgency — the set the goal arbiter promotes from."""
    engine = DriveEngine(now_fn=datetime_from(frozen_clock))
    await engine.bootstrap()
    frozen_clock.advance(4800)
    readings = await engine.step()

    urges = DriveEngine.urges(readings)

    assert {u.drive for u in urges} == _IDLE_FIRING
    urgencies = [u.urgency for u in urges]
    assert urgencies == sorted(urgencies, reverse=True)


# ── TC-3.3: satisfaction lowers a drive ──────────────────────────────────────


async def test_learning_event_lowers_curiosity(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """With curiosity over threshold, a learning event eases it back down (the
    configured ``learning → curiosity -0.45`` relief dominates the tick's accrual)."""
    engine = DriveEngine(now_fn=datetime_from(frozen_clock))
    await engine.bootstrap()
    frozen_clock.advance(4800)
    high = _by_drive(await engine.step())[CURIOSITY].value
    assert high > 0.65  # precondition: curiosity is demanding input

    frozen_clock.advance(10)
    after = _by_drive(await engine.step([DriveEvent(kind=EVENT_LEARNING)]))[CURIOSITY].value

    assert after < high - 0.3  # a real drop, not just the passive decay
    assert after < 0.65  # pulled back under threshold — the need is (briefly) met


async def test_interaction_event_lowers_connection_and_boredom(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """An interaction eases both connection and boredom — the satisfaction map maps
    one event onto every drive it relieves."""
    engine = DriveEngine(now_fn=datetime_from(frozen_clock))
    await engine.bootstrap()
    frozen_clock.advance(4800)
    high = _by_drive(await engine.step())
    connection_high, boredom_high = high[CONNECTION].value, high[BOREDOM].value
    assert connection_high > 0.70 and boredom_high > 0.70

    frozen_clock.advance(10)
    after = _by_drive(await engine.step([DriveEvent(kind=EVENT_INTERACTION)]))

    assert after[CONNECTION].value < connection_high - 0.3
    assert after[CONNECTION].value < 0.70
    assert after[BOREDOM].value < boredom_high  # boredom eased too (weaker weight)


async def test_event_intensity_scales_the_satisfaction(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """A half-intensity interaction relieves connection ~half as much as a full one
    — the applied delta is ``weight × intensity``."""
    engine = DriveEngine(now_fn=datetime_from(frozen_clock))
    await engine.bootstrap()

    # Raise connection, satisfy at half intensity, measure the drop.
    frozen_clock.advance(4800)
    high1 = _by_drive(await engine.step())[CONNECTION].value
    frozen_clock.advance(1)
    half = _by_drive(await engine.step([DriveEvent(kind=EVENT_INTERACTION, intensity=0.5)]))[
        CONNECTION
    ].value
    drop_half = high1 - half

    # Re-raise to a comparable high, satisfy at full intensity, measure again.
    frozen_clock.advance(4800)
    high2 = _by_drive(await engine.step())[CONNECTION].value
    frozen_clock.advance(1)
    full = _by_drive(await engine.step([DriveEvent(kind=EVENT_INTERACTION, intensity=1.0)]))[
        CONNECTION
    ].value
    drop_full = high2 - full

    assert drop_half > 0 and drop_full > drop_half
    # Full intensity satisfies twice as much as half (weight scales linearly).
    assert drop_full == pytest.approx(2 * drop_half, rel=1e-2)


# ── TC-3.7: energy trends toward a sleep signal ──────────────────────────────


async def test_energy_climbs_to_a_sleep_signal_and_rest_restores_it(
    drives_db: AsyncEngine, frozen_clock: FrozenClock
) -> None:
    """Sustained activity drives energy over threshold; its urge is flagged as a
    *sleep* signal (Phase 4 consumes it), not a goal to pursue — and rest restores
    energy back under threshold."""
    engine = DriveEngine(now_fn=datetime_from(frozen_clock))
    await engine.bootstrap()

    frozen_clock.advance(5400)  # 90 min of "active" time
    readings = await engine.step()
    by = _by_drive(readings)
    assert by[ENERGY].over_threshold

    urges = DriveEngine.urges(readings)
    sleep_urges = [u for u in urges if u.is_sleep_signal]
    # Energy is the only sleep signal; the climbing-drive urges are ordinary goals.
    assert [u.drive for u in sleep_urges] == [SLEEP_DRIVE] == [ENERGY]
    assert all(not u.is_sleep_signal for u in urges if u.drive != ENERGY)

    frozen_clock.advance(10)
    rested = _by_drive(await engine.step([DriveEvent(kind=EVENT_REST)]))[ENERGY].value
    assert rested < 0.80  # rest brought tiredness back under the sleep threshold
