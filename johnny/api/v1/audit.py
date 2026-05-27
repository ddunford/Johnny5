"""``GET /api/v1/audit`` — the bus log (every broadcast, incl. dispatched actions).

The Global Workspace persists *every* broadcast to ``workspace_event`` (FC-4: the
observable inner life). This endpoint projects that append-only log — newest
first, optionally filtered by event ``type`` — so the SPA can show what Johnny
did and thought. It includes the FC-5 ``action.dispatched`` dispatch point (every
action, internal or external, is logged there), so this doubles as the action
audit trail.

No new audit store (FC-4/FC-5) — this reads the existing bus log.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from johnny.api.v1.deps import RuntimeDep
from johnny.api.v1.schemas import AuditEvent, AuditResponse

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditResponse)
async def get_audit(
    runtime: RuntimeDep,
    limit: int = Query(default=100, ge=1, le=500, description="Max events to return."),
    type: str | None = Query(default=None, description="Filter to a single event type."),
) -> AuditResponse:
    """Recent bus events, newest first; ``type`` filters to one event kind."""
    events = await runtime.workspace.recent_events(limit, type_filter=type)
    return AuditResponse(
        events=[
            AuditEvent(
                id=event.id,
                ts=event.ts.isoformat() if event.ts else None,
                module=event.module,
                type=event.type,
                payload=dict(event.payload),
            )
            for event in events
        ]
    )
