"""``GET /api/v1/self`` — Johnny's self-model + his recent reflections.

Two reads: the current ``identity`` row (his evolving self-concept — name, the
self-model doc, values, concerns, relationships) and the recent
``self_improvement_note`` rows (Metacognition's reviews). **Read-only** here
(FC-1/FC-9): this exposes the Mind's state for observation; *applying* a
self-improvement proposal is the Phase-9 gated self-edit flow, never this endpoint.

A fresh Mind returns the anchor-grounded v1 seed identity and an empty notes list.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from johnny.api.v1.deps import RuntimeDep
from johnny.api.v1.schemas import IdentityOut, SelfNote, SelfResponse

router = APIRouter(tags=["self"])


@router.get("/self", response_model=SelfResponse)
async def get_self(
    runtime: RuntimeDep,
    notes_limit: int = Query(default=10, ge=1, le=100, description="Max reflections."),
) -> SelfResponse:
    """The current self-model + recent self-improvement notes."""
    doc = await runtime.identity.current()
    identity = (
        IdentityOut(
            name=doc.name,
            version=doc.version,
            self_model_doc=doc.self_model_doc,
            values=list(doc.values),
            concerns=list(doc.concerns),
            relationships=dict(doc.relationships),
        )
        if doc is not None
        else None
    )
    notes = await runtime.metacognition.recent(notes_limit)
    return SelfResponse(
        identity=identity,
        notes=[
            SelfNote(
                ts=n.ts.isoformat() if n.ts else None,
                observation=n.observation,
                proposal=n.proposal,
                status=n.status,
            )
            for n in notes
        ],
    )
