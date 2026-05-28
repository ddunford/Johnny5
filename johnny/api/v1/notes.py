"""``GET /api/v1/notes`` — Johnny's journal, newest first.

A thin read projection of the ``note`` table (written by the ``note`` tool through
the vetted + audited dispatch). The SPA shows his journal/knowledge base; writing
is an autonomous tool action, not an API write — this is read-only (FC-8).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from johnny.api.v1.deps import RuntimeDep
from johnny.api.v1.schemas import NoteItem, NotesResponse

router = APIRouter(tags=["notes"])


@router.get("/notes", response_model=NotesResponse)
async def get_notes(
    runtime: RuntimeDep,
    limit: int = Query(default=50, ge=1, le=200, description="Max notes to return."),
) -> NotesResponse:
    """Recent journal notes, newest first."""
    notes = await runtime.notes.recent(limit)
    return NotesResponse(
        notes=[
            NoteItem(
                id=note.id,
                ts=note.ts.isoformat() if note.ts else None,
                title=note.title,
                body=note.body,
                tags=list(note.tags),
            )
            for note in notes
        ]
    )
