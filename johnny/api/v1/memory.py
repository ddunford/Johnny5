"""``GET /api/v1/memory/episodes`` + ``/memory/facts`` — browse + search his memory.

Two read paths over the Phase-1 memory spine, each with a browse mode (recent,
no query) and a search mode (``?q=``):

* **episodes** — ``EpisodicMemory.recent`` (newest first) / ``EpisodicMemory.recall``
  (the hybrid similarity×recency×salience blend, ``score`` populated);
* **facts** — ``SemanticMemory.recent`` (newest first) / ``SemanticMemory.recall``
  (cosine similarity, ``score`` populated).

Thin projections of existing repository rows — no new domain logic, no new tables.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from johnny.api.v1.deps import RuntimeDep
from johnny.api.v1.schemas import EpisodeOut, EpisodesResponse, FactOut, FactsResponse

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/episodes", response_model=EpisodesResponse)
async def get_episodes(
    runtime: RuntimeDep,
    limit: int = Query(default=20, ge=1, le=200, description="Max episodes to return."),
    q: str | None = Query(default=None, description="Free-text query (hybrid recall)."),
) -> EpisodesResponse:
    """Recent episodes (browse), or the most relevant for ``q`` (search)."""
    if q and q.strip():
        episodes = await runtime.episodic.recall(q, k=limit)
    else:
        episodes = await runtime.episodic.recent(limit)
    return EpisodesResponse(
        episodes=[
            EpisodeOut(
                id=e.id,
                ts=e.ts.isoformat() if e.ts else None,
                kind=e.kind,
                content=e.content,
                actors=list(e.actors),
                emotion_tags=list(e.emotion_tags),
                salience=e.salience,
                score=e.score,
            )
            for e in episodes
        ]
    )


@router.get("/facts", response_model=FactsResponse)
async def get_facts(
    runtime: RuntimeDep,
    limit: int = Query(default=20, ge=1, le=200, description="Max facts to return."),
    q: str | None = Query(default=None, description="Free-text query (similarity recall)."),
) -> FactsResponse:
    """Recent consolidated facts (browse), or the most similar to ``q`` (search)."""
    if q and q.strip():
        facts = await runtime.semantic.recall(q, k=limit)
    else:
        facts = await runtime.semantic.recent(limit)
    return FactsResponse(
        facts=[
            FactOut(
                id=f.id,
                subject=f.subject,
                predicate=f.predicate,
                object=f.object,
                confidence=f.confidence,
                source_episode_ids=list(f.source_episode_ids),
                score=f.score,
            )
            for f in facts
        ]
    )
