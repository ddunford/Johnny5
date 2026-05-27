"""``GET /api/v1/sleeps`` — Johnny's sleep history (consolidation/growth log).

A thin projection of the ``sleep_log`` table (newest first): each row records what
one sleep consolidated, decayed, and merged, the self-model version it produced,
and whether the wake self-check passed. Wraps ``SleepCycle.recent_sleeps``.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from johnny.api.v1.deps import RuntimeDep
from johnny.api.v1.schemas import SleepOut, SleepsResponse

router = APIRouter(tags=["sleeps"])


@router.get("/sleeps", response_model=SleepsResponse)
async def get_sleeps(
    runtime: RuntimeDep,
    limit: int = Query(default=20, ge=1, le=200, description="Max sleeps to return."),
) -> SleepsResponse:
    """Recent sleeps, newest first."""
    sleeps = await runtime.sleep.recent_sleeps(limit)
    return SleepsResponse(
        sleeps=[
            SleepOut(
                id=s.id,
                started_at=s.started_at.isoformat() if s.started_at else None,
                ended_at=s.ended_at.isoformat() if s.ended_at else None,
                trigger=s.trigger,
                facts_written=s.facts_written,
                episodes_decayed=s.episodes_decayed,
                facts_merged=s.facts_merged,
                self_model_version=s.self_model_version,
                snapshot_path=s.snapshot_path,
                self_check_ok=s.self_check_ok,
                notes=dict(s.notes),
            )
            for s in sleeps
        ]
    )
