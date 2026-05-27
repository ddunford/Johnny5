"""``GET /api/v1/state`` — the snapshot the SPA renders on load (FC-8).

The ``/ws/state`` socket is the source of truth for *live* state; this endpoint
returns a one-shot snapshot **equal in shape to a ``/ws/state`` frame's payload**
so the SPA can paint immediately on load, then switch to the live socket. It does
NOT replay a tick or fake a ``ctx`` — it reads the CURRENT state straight off the
runtime's repos (``drives.current()`` / ``affect.current()`` / ``goals.active()`` /
``sleep.latest_sleep()``) and serialises it with ``brain.cycle.serialize_state``,
the exact same projection the live frame uses. One serializer ⇒ no drift.

A fresh Mind serialises cleanly: null mood (he hasn't appraised one yet), no
goals, never slept (``sleep.last`` is null).
"""

from __future__ import annotations

from fastapi import APIRouter

from brain.cycle import serialize_sleep_block, serialize_state, sleep_summary_from_log
from johnny.api.v1.deps import RuntimeDep
from johnny.api.v1.schemas import StateSnapshot

router = APIRouter(tags=["state"])


@router.get("/state", response_model=StateSnapshot)
async def get_state(runtime: RuntimeDep) -> StateSnapshot:
    """Snapshot of Johnny's current drives, mood, goals, tick rate, and sleep status."""
    # Mood: ``affect.current()`` always returns a Mood (baseline before he's ever
    # appraised); represent the never-persisted baseline as null so a fresh Mind
    # reads "no mood yet", matching the live frame (which is null until the first tick).
    current_mood = await runtime.affect.current()
    mood = current_mood if current_mood.id is not None else None

    sleep_block = serialize_sleep_block(
        asleep=runtime.sleep.is_asleep,
        full_agency=runtime.cycle.has_full_agency,
        last=sleep_summary_from_log(await runtime.sleep.latest_sleep()),
    )

    payload = serialize_state(
        tick=runtime.cycle.tick_count,
        drives=await runtime.drives.current(),
        mood=mood,
        goals=await runtime.goals.active(),
        interval=runtime.cycle.next_interval,
        sleep=sleep_block,
    )
    # Validate the serializer's dict through the typed contract — a serializer change
    # that breaks the documented shape fails here instead of shipping silently.
    return StateSnapshot.model_validate(payload)
