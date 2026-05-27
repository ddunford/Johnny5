"""Mood modulates the cycle rate + steers attention (TC-3.4, SPEC §6.2).

Affect isn't cosmetic — it changes *how fast Johnny thinks* and *what he attends
to*. The APPRAISE stage feeds the tick's mood + drives forward to set the next
heartbeat interval and Attention's bias:

* **Cycle rate** — high arousal shortens the interval (excited/anxious → faster);
  the Energy drive's overshoot lengthens it (tired → slower, toward sleep). The
  result is hard-clamped to ``[min, max]`` so no arousal can spin the loop and no
  tiredness can freeze it (the 3.12 runaway-resource guard).
* **Attention bias** — the cycle hands Attention an ``AttentionBias`` carrying the
  mood's arousal + drive-relevant kind pulls.

Driven through the REAL ``CognitiveCycle`` with stub Drives/Affect (a known mood +
drive readings) so the assertions are on the actual wiring, not a reimplementation.
The APPRAISE stage broadcasts drive/mood state, so this is DB+Redis backed → run
in-network via ``./ctl.sh test``. The injected clock + no-op sleep keep it instant.
"""

from __future__ import annotations

import pytest
from helpers.clock import FrozenClock
from helpers.cycle import RecordingAttention, StubAffect, StubDrives, build_cycle

from brain.affect.appraisal import Mood
from brain.drives.engine import DriveReading
from brain.workspace import Workspace


def _energy(value: float) -> DriveReading:
    return DriveReading(
        drive="energy",
        value=value,
        setpoint=0.10,
        accrual_rate=0.0006,
        decay_rate=0.0001,
        threshold=0.80,
    )


def _connection(value: float) -> DriveReading:
    return DriveReading(
        drive="connection",
        value=value,
        setpoint=0.10,
        accrual_rate=0.0005,
        decay_rate=0.0001,
        threshold=0.70,
    )


async def _tick_rate(workspace: Workspace, clock: FrozenClock, mood: Mood, drives) -> float:
    """Run one tick with a known mood + drive readings; return the next interval."""
    harness = build_cycle(
        workspace,
        clock=clock,
        attention=RecordingAttention(),
        drives=StubDrives(drives),
        affect=StubAffect(mood),
    )
    await harness.tick()
    return harness.cycle.next_interval


# ── cycle rate ───────────────────────────────────────────────────────────────


async def test_high_arousal_shortens_the_interval_relative_to_calm(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """An excited (high-arousal) mood makes the heartbeat faster than a calm one."""
    excited = await _tick_rate(workspace, frozen_clock, Mood(valence=0.6, arousal=0.9), [])
    calm = await _tick_rate(workspace, frozen_clock, Mood.baseline(), [])

    assert excited < calm
    # Both stay within the configured bounds (the spin/freeze guard).
    assert 1.5 <= excited <= 12.0
    assert 1.5 <= calm <= 12.0


async def test_tiredness_lengthens_the_interval_toward_sleep(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """Energy over threshold (the sleep precursor) slows the heartbeat, even more so
    against a calm/low-arousal mood — Johnny down-shifts toward rest."""
    tired = await _tick_rate(
        workspace, frozen_clock, Mood(valence=-0.1, arousal=0.2), [_energy(0.90)]
    )
    calm = await _tick_rate(workspace, frozen_clock, Mood.baseline(), [])

    assert tired > calm
    assert 1.5 <= tired <= 12.0


async def test_interval_is_hard_bounded_against_runaway_arousal(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """No mood can drive the rate past its bounds — the 3.12 resource guard. With an
    exaggerated speedup, max arousal still clamps at the floor; with an exaggerated
    slowdown, deep tiredness still clamps at the ceiling."""
    spun = build_cycle(
        workspace,
        clock=frozen_clock,
        attention=RecordingAttention(),
        drives=StubDrives([]),
        affect=StubAffect(Mood(valence=1.0, arousal=1.0)),
        arousal_speedup=50.0,  # would drive the interval far below the floor
    )
    await spun.tick()
    assert spun.cycle.next_interval == pytest.approx(1.5)  # clamped at min

    frozen = build_cycle(
        workspace,
        clock=frozen_clock,
        attention=RecordingAttention(),
        drives=StubDrives([_energy(0.99)]),
        affect=StubAffect(Mood(valence=-0.5, arousal=0.0)),
        tired_slowdown=50.0,  # would drive the interval far above the ceiling
    )
    await frozen.tick()
    assert frozen.cycle.next_interval == pytest.approx(12.0)  # clamped at max


# ── attention bias ───────────────────────────────────────────────────────────


async def test_cycle_hands_attention_the_mood_and_drive_bias(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """The APPRAISE stage builds Attention's bias from the mood's arousal and the
    unmet drives' kind pulls (an over-threshold Connection pulls toward ``input``)."""
    attention = RecordingAttention()
    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        attention=attention,
        drives=StubDrives([_connection(0.85)]),
        affect=StubAffect(Mood(valence=0.2, arousal=0.8)),
    )

    await harness.tick()

    assert attention.biases, "the cycle never set an attention bias"
    bias = attention.biases[-1]
    assert bias.arousal == pytest.approx(0.8)  # the mood's arousal flows through
    assert bias.kind_boosts.get("input", 0.0) > 0.0  # unmet Connection pulls input


async def test_no_drives_over_threshold_leaves_no_kind_boosts(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """With nothing over threshold, the bias carries only arousal — no kind pulls."""
    attention = RecordingAttention()
    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        attention=attention,
        drives=StubDrives([_connection(0.30)]),  # under threshold
        affect=StubAffect(Mood(valence=0.0, arousal=0.5)),
    )

    await harness.tick()

    assert attention.biases[-1].kind_boosts == {}
