"""Memory wiring for the cycle — the RECALL and LEARN stages (``SPEC §7`` 4 & 9).

These bridge the cognitive cycle to the Phase-1 memory spine; they are stage
collaborators, not bus agents (memory is mechanical infrastructure with no prompt,
so it isn't an editable inner agent).

* **RECALL** (``MemoryRecaller``) builds a query from the current salient focus
  and pulls relevant episodes + facts into the workspace. Recalled memory is
  scaled *below* a fresh message's salience: it informs the present tick (the
  "I keep coming back to that thing Dan said" effect) without crowding out what's
  happening now. The query is built from non-ambient focus where possible, so
  recall keys off what matters, not the idle clock line — and never off recalled
  items (recall runs before they're merged, so it can't feed on itself).

* **LEARN** (``EpisodicLearner``) writes an episode *of note*: every interaction
  is remembered immediately; an idle stream-of-consciousness tick is written only
  on a slow sub-cadence, so episodic memory grows from what's notable rather than
  from every few seconds of "nothing happened" (Phase 4 sleep consolidates it).

Both embed exclusively through the injected memory stores (FC-4); a recall/write
failure degrades the stage, never the heartbeat (the cycle isolates it).
"""

from __future__ import annotations

from collections.abc import Sequence

from brain.affect.appraisal import Mood
from brain.memory.base import RecallWeights, clamp01
from brain.memory.episodic import Episode, EpisodicMemory
from brain.memory.semantic import SemanticFact, SemanticMemory
from brain.workspace import WorkspaceItem
from foundation.config import get_settings

_AMBIENT_KIND = "ambient"
_INPUT_KIND = "input"
# Arousal at the calm baseline — recall salience is boosted only above this.
_AROUSAL_NEUTRAL = 0.3

# Saliences for written episodes — an interaction matters more than an idle muse.
_INTERACTION_SALIENCE = 0.75
_REFLECTION_SALIENCE = 0.5
# How many top focus items seed the recall query.
_QUERY_FOCUS_ITEMS = 3


class MemoryRecaller:
    """RECALL stage — pull relevant episodes + facts into the workspace."""

    def __init__(
        self,
        *,
        episodic: EpisodicMemory | None = None,
        semantic: SemanticMemory | None = None,
        episodes_k: int | None = None,
        facts_k: int | None = None,
        salience_ceiling: float | None = None,
    ) -> None:
        settings = get_settings()
        self._episodic = episodic or EpisodicMemory()
        self._semantic = semantic or SemanticMemory()
        self._episodes_k = (
            episodes_k if episodes_k is not None else settings.memory_recall_episodes_k
        )
        self._facts_k = facts_k if facts_k is not None else settings.memory_recall_facts_k
        self._ceiling = (
            salience_ceiling
            if salience_ceiling is not None
            else settings.memory_recall_salience_ceiling
        )
        self._base_weights = RecallWeights.from_settings(settings)
        self._arousal_gain = settings.memory_recall_arousal_salience_gain
        # Current mood, set by the cycle each tick (optional). Arousal biases the
        # recall blend toward salience so charged memories surface more readily.
        self._mood: Mood | None = None

    def set_mood(self, mood: Mood | None) -> None:
        """Set the mood biasing the next recall (called by the cycle, FC-7 slot)."""
        self._mood = mood

    async def recall(self, *, focus: Sequence[WorkspaceItem]) -> Sequence[WorkspaceItem]:
        """Recall episodes + facts relevant to the current focus."""
        query = self._build_query(focus)
        if not query:
            return []
        episodes = await self._episodic.recall(
            query, k=self._episodes_k, weights=self._biased_weights()
        )
        facts = await self._semantic.recall(query, k=self._facts_k)
        return [*self._episodes_to_items(episodes), *self._facts_to_items(facts)]

    def _biased_weights(self) -> RecallWeights | None:
        """Boost the salience weight by arousal above baseline (``SPEC §6.2``).

        ``None`` (the episodic default) when no mood is set or arousal is at rest,
        so unbiased recall is unchanged; an activated Johnny weights emotionally
        charged (high-salience) episodes more heavily.
        """
        if self._mood is None:
            return None
        excess = max(0.0, self._mood.arousal - _AROUSAL_NEUTRAL)
        if excess <= 0.0:
            return None
        base = self._base_weights
        return RecallWeights(
            similarity=base.similarity,
            recency=base.recency,
            salience=base.salience * (1.0 + self._arousal_gain * excess),
            recency_halflife_seconds=base.recency_halflife_seconds,
        )

    def _build_query(self, focus: Sequence[WorkspaceItem]) -> str:
        """Seed the recall query from the most salient non-ambient focus items."""
        notable = [i for i in focus if i.kind != _AMBIENT_KIND]
        chosen = notable or list(focus)
        if not chosen:
            return ""
        chosen = sorted(chosen, key=lambda i: i.salience, reverse=True)[:_QUERY_FOCUS_ITEMS]
        return " ".join(i.content for i in chosen).strip()

    def _episodes_to_items(self, episodes: Sequence[Episode]) -> list[WorkspaceItem]:
        if not episodes:
            return []
        # Rank-relative normalisation: the blended recall score is unbounded above
        # (weighted sum), so scale within the batch into the [0, ceiling] band.
        top = max((ep.score or 0.0) for ep in episodes) or 1.0
        items: list[WorkspaceItem] = []
        for ep in episodes:
            relevance = (ep.score or 0.0) / top
            items.append(
                WorkspaceItem(
                    kind="memory",
                    content=ep.content,
                    salience=clamp01(relevance * self._ceiling),
                    source="episodic",
                    metadata={"episode_id": ep.id, "episode_kind": ep.kind},
                )
            )
        return items

    def _facts_to_items(self, facts: Sequence[SemanticFact]) -> list[WorkspaceItem]:
        items: list[WorkspaceItem] = []
        for fact in facts:
            # Facts carry a [0, 1] similarity score (no recency term — consolidated
            # knowledge, not timestamped events).
            text = f"{fact.subject} {fact.predicate} {fact.object}"
            items.append(
                WorkspaceItem(
                    kind="fact",
                    content=text,
                    salience=clamp01((fact.score or 0.0) * self._ceiling),
                    source="semantic",
                    metadata={"fact_id": fact.id},
                )
            )
        return items


class EpisodicLearner:
    """LEARN stage — write an episode worth remembering."""

    def __init__(
        self,
        *,
        episodic: EpisodicMemory | None = None,
        idle_every_ticks: int | None = None,
    ) -> None:
        settings = get_settings()
        self._episodic = episodic or EpisodicMemory()
        self._idle_every = max(1, idle_every_ticks or settings.memory_learn_idle_every_ticks)
        self._ticks = 0

    async def learn(self, *, contents: Sequence[WorkspaceItem], thought: str | None) -> None:
        """Write an episode for an interaction always; for an idle tick rarely."""
        self._ticks += 1
        inputs = [i for i in contents if i.kind == _INPUT_KIND]

        if inputs:
            await self._write_interaction(inputs, thought)
        elif thought and self._ticks % self._idle_every == 0:
            await self._write_reflection(thought)

    async def _write_interaction(
        self, inputs: Sequence[WorkspaceItem], thought: str | None
    ) -> None:
        heard = " / ".join(i.content for i in inputs)
        content = f"Dan said: {heard}." + (f" I thought: {thought}" if thought else "")
        await self._episodic.write(
            Episode(
                kind="interaction",
                content=content,
                actors=["dan", "johnny"],
                salience=_INTERACTION_SALIENCE,
            )
        )

    async def _write_reflection(self, thought: str) -> None:
        await self._episodic.write(
            Episode(
                kind="reflection",
                content=thought,
                actors=["johnny"],
                salience=_REFLECTION_SALIENCE,
            )
        )
