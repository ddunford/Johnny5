"""Metacognition review: proposes self-improvements, never applies them (TC-4.5).

Each sleep, ``Metacognition.review(window)`` reflects on how Johnny has been
functioning and writes zero-or-more ``self_improvement_note`` rows (``status=open``).
The defining invariant of this phase is the **negative** one: a review *proposes*
but never *applies* — applying a proposal (editing a prompt, retuning a drive,
changing code) is the Phase-9 gated self-edit flow, deliberately not wired here.

This suite pins, with a stubbed ``metacognition`` router (the projection is locked by
``test_metacognition_contract.py``):
* one ``open`` note per proposal, grounded in the actual review window (provenance);
* **nothing is applied** — a seeded drive is unchanged, and no goal/mood/thought is
  written; the review's only side effect is the notes;
* a tired review writes no notes (degrade);
* a clean review (no proposals) still returns its reflection and writes nothing.

DB-backed (``self_improvement_note`` persists) → run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.affect.agent import MoodRow
from brain.agents.narrator import ThoughtRow
from brain.drives.engine import DriveEngine
from brain.goals.store import GoalRow
from brain.llm.base import Completion, LLMUnavailableError, Message
from brain.metacognition.agent import Metacognition, Proposal, Review, ReviewWindow
from brain.metacognition.store import STATUS_OPEN, MetacognitionStore
from foundation.db import session_scope

_REVIEW = Review(
    reflection="I abandoned more than I resolved; I over-commit when curiosity spikes.",
    proposals=[
        Proposal(
            observation="3 of 5 curiosity goals were abandoned",
            proposal="raise the curiosity goal threshold",
        ),
        Proposal(
            observation="I degraded on 2 ticks when Groq was down",
            proposal="prefer the local model for deliberation",
        ),
    ],
)

_WINDOW = ReviewWindow(
    goals_resolved=2,
    goals_abandoned=3,
    degraded_ticks=2,
    recent_goals=["satisfy curiosity about the rig", "reach out to Dan"],
    mood="restless",
    drives="curiosity high",
)


class _ReviewRouter:
    """Returns a fixed metacognition Review JSON completion."""

    def __init__(self, review: Review) -> None:
        self._content = review.model_dump_json()
        self.roles: list[str] = []

    async def complete(
        self,
        role: str,
        messages: Sequence[Message],
        *,
        schema: type | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Completion:
        self.roles.append(role)
        return Completion(content=self._content, provider="canned", model="canned-model")


class _TiredRouter:
    async def complete(
        self, role: str, messages: Sequence[Message], **_kwargs: object
    ) -> Completion:
        raise LLMUnavailableError(role)


class _StubPromptStore:
    def load_prompt(self, name: str) -> str:
        return "Review how your mind has been working and propose improvements."


def _metacognition(router: object) -> Metacognition:
    return Metacognition(
        router=router,  # type: ignore[arg-type]  # duck-typed router double
        store=MetacognitionStore(),
        config_store=_StubPromptStore(),  # type: ignore[arg-type]  # duck-typed config double
    )


async def _count(model: type) -> int:
    async with session_scope() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_review_writes_one_open_note_per_proposal_grounded_in_the_window(
    metacognition_db: AsyncEngine,
) -> None:
    metacog = _metacognition(_ReviewRouter(_REVIEW))

    review = await metacog.review(_WINDOW)

    assert review.reflection == _REVIEW.reflection
    notes = await MetacognitionStore().recent(limit=10)
    assert len(notes) == len(_REVIEW.proposals)  # one note per proposal
    assert {n.observation for n in notes} == {p.observation for p in _REVIEW.proposals}
    assert all(n.status == STATUS_OPEN for n in notes)  # informational only — not applied
    # Grounded: each note carries the actual review window + reflection as provenance.
    for note in notes:
        assert note.source["window"] == _WINDOW.model_dump()
        assert note.source["reflection"] == _REVIEW.reflection


async def test_review_proposes_but_never_applies(metacognition_db: AsyncEngine) -> None:
    """The Phase-4 invariant: a review records proposals but mutates nothing — a
    seeded drive is untouched and no goal/mood/thought is written. The only side
    effect is the ``self_improvement_note`` rows (applying is Phase 9)."""
    # Seed the real drive state; capture curiosity before the review.
    await DriveEngine().bootstrap()
    before = {r.drive: r.value for r in await DriveEngine().current()}

    # A proposal that literally suggests retuning curiosity — it must NOT be applied.
    metacog = _metacognition(_ReviewRouter(_REVIEW))
    await metacog.review(_WINDOW)

    after = {r.drive: r.value for r in await DriveEngine().current()}
    assert after == before  # no drive was retuned — the proposal was not applied

    # The review's ONLY write is the notes — nothing else was enacted.
    assert await _count(GoalRow) == 0  # no goal created/changed
    assert await _count(MoodRow) == 0  # no mood written
    assert await _count(ThoughtRow) == 0  # no thought written
    notes = await MetacognitionStore().recent(limit=10)
    assert len(notes) == len(_REVIEW.proposals)
    assert all(n.status == STATUS_OPEN for n in notes)


async def test_tired_review_writes_no_notes(metacognition_db: AsyncEngine) -> None:
    """Every provider tired → an empty reflection, zero proposals, zero notes — the
    stage degrades and the sleep pipeline still wakes."""
    metacog = _metacognition(_TiredRouter())

    review = await metacog.review(_WINDOW)

    assert review.proposals == []
    assert await MetacognitionStore().recent(limit=10) == []


async def test_clean_review_with_no_proposals_writes_nothing(
    metacognition_db: AsyncEngine,
) -> None:
    """A review that finds nothing to change still returns its reflection but writes
    no notes."""
    clean = Review(reflection="Nothing to change this cycle.", proposals=[])
    metacog = _metacognition(_ReviewRouter(clean))

    review = await metacog.review(_WINDOW)

    assert review.reflection == "Nothing to change this cycle."
    assert await MetacognitionStore().recent(limit=10) == []
