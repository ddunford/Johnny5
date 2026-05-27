"""Mood survives a restart and reloads on a fresh Affect (TC-3.5 mood slice, FC-6).

Continuity of *feeling*: Johnny doesn't wake up emotionally blank. The ``mood``
table is append-only and a row is written only on a real shift; ``Affect.current()``
reloads the latest row on first access, so a fresh ``Affect`` after a restart
resumes the mood it held — not the calm baseline.

This is the mood slice of TC-3.5; the drive slice is in ``test_drive_persistence``
and the in-flight-goal-resume slice lands once the goal arbiter (3.5) is in.

``simulate_restart`` rebuilds the process-global engine (the in-process stand-in
for ``./ctl.sh down && up``); the frozen clock + rule-based appraisal keep the mood
shift deterministic. DB-backed → run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.affect.agent import Affect
from brain.affect.appraisal import Mood
from brain.drives.engine import EVENT_FAILURE, DriveEvent


async def test_mood_starts_at_calm_baseline_with_no_history(drives_db: AsyncEngine) -> None:
    """On a clean DB (no mood rows) the current mood is the calm-neutral baseline."""
    affect = Affect()
    current = await affect.current()
    baseline = Mood.baseline()
    assert current.id is None
    assert current.valence == pytest.approx(baseline.valence)
    assert current.arousal == pytest.approx(baseline.arousal)
    assert current.emotions == {}


async def test_mood_survives_restart_and_reloads_on_a_fresh_instance(
    drives_db: AsyncEngine,
    simulate_restart: Callable[[], Awaitable[AsyncEngine]],
    frozen_clock: FrozenClock,
) -> None:
    now_fn = datetime_from(frozen_clock)
    affect = Affect(now_fn=now_fn)

    # A failure event swings mood negative enough to persist a row (real shift).
    failure = [DriveEvent(kind=EVENT_FAILURE)]
    mood = await affect.appraise_tick(contents=[], drives=[], events=failure)
    assert mood.valence < 0.0  # appraised unpleasant
    assert mood.id is not None  # a row was written (moved off baseline)
    assert mood.emotions  # frustration tagged
    persisted_valence = mood.valence
    persisted_emotions = set(mood.emotions)

    # Tear down every engine/connection and reconnect — only on-disk data survives.
    await simulate_restart()

    # A fresh Affect reloads the last persisted mood on first access (continuity).
    revived = Affect(now_fn=now_fn)
    current = await revived.current()
    assert current.id == mood.id  # the same persisted row, not a blank baseline
    assert current.valence == pytest.approx(persisted_valence)
    assert current.arousal == pytest.approx(mood.arousal)
    assert set(current.emotions) == persisted_emotions
