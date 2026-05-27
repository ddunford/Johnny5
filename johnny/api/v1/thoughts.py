"""``GET /api/v1/thoughts`` — recent inner monologue (history for the SPA).

The ``/ws/consciousness`` socket streams thoughts live; this endpoint returns the
recent history (newest first) so a panel can render before the next thought
arrives. A thin projection of the ``workspace_event`` bus log filtered to
``type=thought`` — the same rows the WS backfill replays, same wire shape.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from johnny.api.v1.deps import RuntimeDep
from johnny.api.v1.schemas import Thought, ThoughtsResponse
from johnny.api.ws import THOUGHT_EVENT

router = APIRouter(tags=["thoughts"])


@router.get("/thoughts", response_model=ThoughtsResponse)
async def get_thoughts(
    runtime: RuntimeDep,
    limit: int = Query(default=50, ge=1, le=500, description="Max thoughts to return."),
) -> ThoughtsResponse:
    """Recent thoughts, newest first."""
    events = await runtime.workspace.recent_events(limit, type_filter=THOUGHT_EVENT)
    return ThoughtsResponse(
        thoughts=[
            Thought(
                id=event.id,
                ts=event.ts.isoformat() if event.ts else None,
                text=str(event.payload.get("text", "")),
            )
            for event in events
        ]
    )
