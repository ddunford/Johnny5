"""``GET /api/v1/goals`` — what Johnny is pursuing now + what he's recently closed.

A thin projection of the ``goal`` table: ``active`` (current goals, incumbent
first) and ``recent`` (recently resolved/abandoned goals, newest first — the goal
history). No new domain logic — wraps ``GoalStore.active`` / ``recent_closed``.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Query

from brain.goals.store import Goal
from johnny.api.v1.deps import RuntimeDep
from johnny.api.v1.schemas import GoalOut, GoalsResponse

router = APIRouter(tags=["goals"])


def _to_out(goals: Sequence[Goal]) -> list[GoalOut]:
    return [
        GoalOut(
            id=g.id,
            source=g.source,
            description=g.description,
            priority=g.priority,
            status=g.status,
            plan=dict(g.plan),
            outcome=dict(g.outcome),
            created_at=g.created_at.isoformat() if g.created_at else None,
            resolved_at=g.resolved_at.isoformat() if g.resolved_at else None,
        )
        for g in goals
    ]


@router.get("/goals", response_model=GoalsResponse)
async def get_goals(
    runtime: RuntimeDep,
    limit: int = Query(default=20, ge=1, le=200, description="Max recently-closed goals."),
) -> GoalsResponse:
    """Active goals + the recently closed goal history."""
    return GoalsResponse(
        active=_to_out(await runtime.goals.active()),
        recent=_to_out(await runtime.goals.recent_closed(limit)),
    )
