"""Drives survive a restart and accrual is exact across downtime (TC-3.5, FC-6).

The continuity guarantee: Johnny doesn't reset to a blank emotional/motivational
state on reboot. ``drive_state`` persists each drive's pressure *and* its
``updated_at``, so after a restart the engine resumes from the persisted value and
the elapsed-time accrual picks up exactly where it left off — pressure that built
over downtime is honoured, not lost.

This covers the *drive* slice of TC-3.5; the mood-history and in-flight-goal-resume
slices are added once Affect (3.3) and the goal arbiter (3.5) land.

``simulate_restart`` tears down and rebuilds the process-global engine (the
in-process stand-in for ``./ctl.sh down && up``), so a follow-up read comes from
disk via brand-new connections — exactly the memory-spine restart pattern. The
frozen clock is shared across the restart so "time kept passing" is deterministic.
DB-backed → run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.drives import DriveEngine, DriveReading
from brain.drives.parameters import CONTINUITY, CURIOSITY


def _by_drive(readings: list[DriveReading]) -> dict[str, DriveReading]:
    return {r.drive: r for r in readings}


async def test_drives_survive_restart_and_accrual_continues_from_persisted_state(
    drives_db: AsyncEngine,
    simulate_restart: Callable[[], Awaitable[AsyncEngine]],
    frozen_clock: FrozenClock,
) -> None:
    now_fn = datetime_from(frozen_clock)
    engine = DriveEngine(now_fn=now_fn)
    await engine.bootstrap()

    # Build real pressure: an hour idle pushes curiosity well over threshold.
    frozen_clock.advance(3600)
    curiosity_before = _by_drive(await engine.step())[CURIOSITY].value
    assert curiosity_before > 0.65  # non-default state worth preserving

    # Tear down every engine/connection and reconnect — only on-disk data survives.
    await simulate_restart()

    # A fresh engine reads the persisted pressure WITHOUT advancing: not reset to
    # setpoint, and continuity is still grounded across the reboot.
    engine_after = DriveEngine(now_fn=now_fn)
    persisted = _by_drive(await engine_after.current())
    assert persisted[CURIOSITY].value == pytest.approx(curiosity_before)
    assert persisted[CURIOSITY].value > 0.65
    assert not persisted[CONTINUITY].over_threshold

    # Accrual is exact across downtime: another hour keeps climbing toward the
    # ~0.82 equilibrium from the persisted value — never from zero, never past eq.
    frozen_clock.advance(3600)
    curiosity_after = _by_drive(await engine_after.step())[CURIOSITY].value
    assert curiosity_after > curiosity_before
    assert curiosity_after < 0.821
