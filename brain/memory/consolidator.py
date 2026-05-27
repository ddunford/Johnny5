"""Consolidation ("sleep") — episodic → semantic. **Stub this phase.**

Consolidation is what makes Johnny *grow* rather than just accumulate logs
(``SPEC §8`` / the Generative Agents reflection step; MemGPT's known gap is
having *no* automatic consolidation). The real pass — embedding-based clustering,
LLM summarisation routed through the ``consolidation`` role (Groq), decay/merge,
self-model refresh — lands in **Phase 4**.

This phase ships the *interface plus a naive, callable pass*: it pulls recent
episodes, clusters them by ``kind``, writes one low-confidence semantic fact per
cluster carrying the source episode ids as provenance. Deterministic and
LLM-free so it runs in tests; ``_summarise`` is the documented seam Phase 4
swaps for the router-backed summariser.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from brain.memory.episodic import EpisodeRepository, EpisodeRow
from brain.memory.semantic import SemanticFact, SemanticMemory
from foundation.db import session_scope

# Naive summaries stay short; the real Phase 4 summariser produces proper prose.
_SUMMARY_MAX_CHARS = 480
_STUB_CONFIDENCE = 0.3
_CONSOLIDATION_PREDICATE = "recent_experience"


class Consolidator:
    """Naive episodic→semantic consolidation (Phase 1 stub).

    Callable, not scheduled — Phase 4 wires it to the sleep cadence.
    """

    def __init__(self, semantic: SemanticMemory, *, recent_limit: int = 50) -> None:
        self._semantic = semantic
        self._recent_limit = recent_limit

    async def run(self, *, limit: int | None = None) -> list[SemanticFact]:
        """Summarise recent episodes into semantic facts; return the facts written.

        Clusters the most recent ``limit`` episodes by ``kind`` and upserts one
        fact per cluster (``subject = kind``), so each fact references the
        episode ids it was distilled from.
        """
        cap = limit or self._recent_limit
        async with session_scope() as session:
            recent = await EpisodeRepository(session).recent(cap)
        if not recent:
            return []

        clusters: dict[str, list[EpisodeRow]] = defaultdict(list)
        for episode in recent:
            clusters[episode.kind].append(episode)

        facts: list[SemanticFact] = []
        for kind, episodes in clusters.items():
            fact = await self._semantic.upsert_fact(
                subject=kind,
                predicate=_CONSOLIDATION_PREDICATE,
                obj=self._summarise(kind, episodes),
                confidence=_STUB_CONFIDENCE,
                source_episode_ids=[e.id for e in episodes],
            )
            facts.append(fact)
        return facts

    def _summarise(self, kind: str, episodes: Sequence[EpisodeRow]) -> str:
        """Naive cluster summary. Phase 4 replaces this with a router call
        (role ``consolidation``) over the cluster's contents."""
        snippets = "; ".join(e.content for e in episodes)
        summary = f"Across {len(episodes)} recent '{kind}' episodes: {snippets}"
        return summary[:_SUMMARY_MAX_CHARS]
