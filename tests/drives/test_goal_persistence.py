"""An in-flight goal resumes after a restart (TC-3.5 goal slice, SPEC §6.3, FC-6).

Goals persist across restarts so Johnny resumes a pursuit rather than starting
blank. And because the anti-thrash dwell keys off the goal's persisted
``created_at`` (not in-memory arbiter state), the hysteresis survives the reboot
too — a competitor that appears just after a restart still can't immediately
displace the resumed goal.

This is the goal slice of TC-3.5; the drive + mood slices are in
``test_drive_persistence`` / ``test_mood_persistence``. DB-backed → run in-network
via ``./ctl.sh test``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.affect.appraisal import Mood
from brain.drives.engine import Urge
from brain.goals.arbiter import GoalArbiter
from brain.goals.store import STATUS_ACTIVE, GoalStore

_MOOD = Mood(valence=0.0, arousal=0.5)


def _urge(drive: str, urgency: float, *, threshold: float = 0.65) -> Urge:
    value = threshold + urgency * (1.0 - threshold)
    return Urge(drive=drive, value=value, threshold=threshold, urgency=urgency)


async def test_active_goal_resumes_after_restart_and_keeps_its_dwell(
    drives_db: AsyncEngine,
    simulate_restart: Callable[[], Awaitable[AsyncEngine]],
    frozen_clock: FrozenClock,
) -> None:
    now_fn = datetime_from(frozen_clock)
    arbiter = GoalArbiter(store=GoalStore(now_fn=now_fn), now_fn=now_fn)

    pursued = await arbiter.arbitrate([_urge("curiosity", 0.70)], _MOOD)
    assert pursued is not None and pursued.source == "curiosity"
    goal_id = pursued.id

    # Tear down every engine/connection and reconnect — only on-disk data survives.
    await simulate_restart()

    # The in-flight pursuit reloads — Johnny resumes it, not a blank slate.
    active = await GoalStore(now_fn=now_fn).active()
    assert [g.id for g in active] == [goal_id]
    assert active[0].source == "curiosity"
    assert active[0].status == STATUS_ACTIVE

    # The dwell (created_at-based) survived the reboot: a stronger competitor that
    # appears within the window still can't displace the resumed goal.
    frozen_clock.advance(10)  # < 45s dwell, measured from the persisted created_at
    arbiter_after = GoalArbiter(store=GoalStore(now_fn=now_fn), now_fn=now_fn)
    held = await arbiter_after.arbitrate([_urge("connection", 0.99)], _MOOD)
    assert held is not None
    assert held.id == goal_id  # hysteresis persisted across the restart
