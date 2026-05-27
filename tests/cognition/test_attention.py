"""TC-2.3 — Attention is a bottleneck, not a pass-through.

The phase's core design constraint (LIDA / Global Workspace Theory): flooding the
workspace with low-salience content degrades cognition, so Attention must admit
only a **bounded** salient set. These assert the invariant directly on the pure,
in-memory ``Attention.select`` (no loop, no DB, no LLM):

* a flood of candidates never yields more than the capacity bound;
* a high-salience interrupt (a message) beats many ambient items;
* duplicate content collapses to a single slot (no wasted capacity);
* a line repeated across ticks loses to a fresh one (novelty drift — Johnny
  doesn't fixate on the same idle thought forever).
"""

from __future__ import annotations

from brain.agents.attention import Attention
from brain.workspace import WorkspaceItem


def _ambient(n: int, *, salience: float = 0.1) -> list[WorkspaceItem]:
    """``n`` distinct low-salience ambient items (the trivia Attention must shed)."""
    return [
        WorkspaceItem(kind="ambient", content=f"ambient line {i}", salience=salience)
        for i in range(n)
    ]


async def test_selection_is_bounded_however_many_candidates() -> None:
    """20 candidates into a capacity-3 workspace → never more than 3 admitted, and
    the bound holds tick after tick (the workspace can't grow unbounded)."""
    attention = Attention(capacity=3, weight_salience=1.0, weight_novelty=0.0)

    for _ in range(5):  # several ticks
        selected = await attention.select(working_memory=_ambient(20), percepts=[])
        assert len(selected) == 3
        assert len(selected) <= 3


async def test_salient_input_beats_a_flood_of_ambient() -> None:
    """A high-salience message wins attention; trivial ambient items are excluded."""
    attention = Attention(capacity=3, weight_salience=1.0, weight_novelty=0.0)
    message = WorkspaceItem(kind="input", content="I just adopted a dog named Pixel", salience=0.95)

    selected = await attention.select(working_memory=_ambient(10), percepts=[message])
    contents = [item.content for item in selected]

    assert len(selected) == 3
    # The interrupt made it in...
    assert "I just adopted a dog named Pixel" in contents
    # ...and most of the ambient flood was shed (only 2 slots left after the input).
    excluded = [item.content for item in _ambient(10) if item.content not in contents]
    assert len(excluded) == 8
    # The admitted message carries the highest attention score (it leads the set).
    assert selected[0].content == "I just adopted a dog named Pixel"


async def test_duplicate_content_collapses_to_one_slot() -> None:
    """The same content arriving as both a percept and a working-memory item must
    not waste two slots — it dedupes to one, keeping the higher salience."""
    attention = Attention(capacity=5, weight_salience=1.0, weight_novelty=0.0)
    same_low = WorkspaceItem(kind="ambient", content="the fridge is humming", salience=0.2)
    same_high = WorkspaceItem(kind="memory", content="the fridge is humming", salience=0.8)

    selected = await attention.select(working_memory=[same_high], percepts=[same_low])

    assert len(selected) == 1
    # Kept the higher-salience instance (its score, salience-only here, ≈ 0.8).
    assert selected[0].content == "the fridge is humming"
    assert selected[0].salience > 0.5


async def test_repeated_line_loses_to_a_fresh_one() -> None:
    """Novelty drift: a line surfaced on recent ticks is penalised, so an equally
    intrinsically-salient *fresh* line outranks it — idle attention keeps moving."""
    attention = Attention(
        capacity=1,
        weight_salience=1.0,
        weight_novelty=0.6,
        repeat_penalty=0.5,
        novelty_horizon=20,
    )
    stale = WorkspaceItem(kind="ambient", content="it is quiet", salience=0.5)

    # Surface the stale line over several ticks so it accumulates in the recent window.
    for _ in range(3):
        await attention.select(working_memory=[stale], percepts=[])

    fresh = WorkspaceItem(kind="ambient", content="a door just closed", salience=0.5)
    selected = await attention.select(working_memory=[stale], percepts=[fresh])

    # Equal intrinsic salience, but the fresh line wins the single slot on novelty.
    assert len(selected) == 1
    assert selected[0].content == "a door just closed"
