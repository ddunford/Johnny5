"""Attention — the salience bottleneck (``SPEC §5`` #2, the phase's core constraint).

Attention is **not a pass-through**. Per LIDA / Global Workspace Theory, flooding
the workspace with low-salience content *degrades* cognition — a wider workspace
is worse, not better. So Attention scores every candidate (working-memory items +
new percepts) and admits only a **bounded** top-k salient set onto the blackboard.
That bound is the single most important design rule of the phase; everything
downstream (recall, narration) reasons over the small set Attention let through.

Phase-2 salience blends two signals available now:

* **Intrinsic salience** — the item's own weight (a message is high, an ambient
  metric is low), so an interrupt wins attention.
* **Novelty** — content surfaced on recent ticks is penalised, so Johnny doesn't
  fixate on the same idle line forever and idle attention keeps drifting.

Goal-relevance, drive pressure, and emotional charge join the blend in Phase 3
(Affect/Drives) — the scoring seam is here, those terms slot in. Attention makes
no LLM call this phase: salience selection is the deterministic LIDA mechanism;
the LLM relevance judgement is a later refinement. Pipeline-driven, so it
subscribes to no bus events.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from brain.memory.base import clamp01
from brain.workspace import WorkspaceEvent, WorkspaceItem
from foundation.config import get_settings

ATTENTION_AGENT_NAME = "attention"


class Attention:
    """Scores candidates and selects a bounded salient set into the workspace."""

    name = ATTENTION_AGENT_NAME
    subscribes_to: Sequence[str] = ()
    prompt = ""  # deterministic salience scoring this phase — no LLM call
    model_route = "attention"  # reserved for the Phase-3 relevance judgement

    def __init__(
        self,
        *,
        capacity: int | None = None,
        weight_salience: float | None = None,
        weight_novelty: float | None = None,
        repeat_penalty: float | None = None,
        novelty_horizon: int | None = None,
    ) -> None:
        settings = get_settings()
        self._capacity = capacity if capacity is not None else settings.workspace_capacity
        self._w_salience = (
            weight_salience if weight_salience is not None else settings.attention_weight_salience
        )
        self._w_novelty = (
            weight_novelty if weight_novelty is not None else settings.attention_weight_novelty
        )
        self._repeat_penalty = (
            repeat_penalty if repeat_penalty is not None else settings.attention_repeat_penalty
        )
        # Remember a few ticks' worth of surfaced content to detect repetition.
        horizon = novelty_horizon if novelty_horizon is not None else max(self._capacity * 4, 12)
        self._recent: deque[str] = deque(maxlen=horizon)

    async def select(
        self,
        *,
        working_memory: Sequence[WorkspaceItem],
        percepts: Sequence[WorkspaceItem],
    ) -> Sequence[WorkspaceItem]:
        """Score working memory + percepts; admit the bounded top-k salient set."""
        candidates = self._dedupe([*percepts, *working_memory])
        scored = [(self._score(item), item) for item in candidates]
        scored.sort(key=lambda pair: pair[0], reverse=True)

        selected: list[WorkspaceItem] = []
        for score, item in scored[: self._capacity]:
            # The admitted item carries its attention score as its salience, so
            # everything downstream orders by what Attention judged salient *now*.
            selected.append(item.model_copy(update={"salience": clamp01(score)}))

        self._remember([item.content for item in selected])
        return selected

    async def handle(self, event: WorkspaceEvent) -> Sequence[WorkspaceEvent]:
        """Pipeline-driven agent — reacts to no bus events."""
        return ()

    # ── scoring ────────────────────────────────────────────────────────────────

    def _score(self, item: WorkspaceItem) -> float:
        """Blend intrinsic salience with a novelty bonus (repetition penalised)."""
        return self._w_salience * item.salience + self._w_novelty * self._novelty(item.content)

    def _novelty(self, content: str) -> float:
        """1.0 for unseen content; decays as it recurs in the recent window."""
        repeats = self._recent.count(content)
        if repeats == 0:
            return 1.0
        return self._repeat_penalty**repeats

    def _remember(self, contents: Sequence[str]) -> None:
        for content in contents:
            self._recent.append(content)

    @staticmethod
    def _dedupe(items: Sequence[WorkspaceItem]) -> list[WorkspaceItem]:
        """Collapse duplicate content (a new percept also sits in working memory),
        keeping the highest-salience instance so a slot isn't wasted on a repeat."""
        best: dict[str, WorkspaceItem] = {}
        for item in items:
            existing = best.get(item.content)
            if existing is None or item.salience > existing.salience:
                best[item.content] = item
        return list(best.values())
