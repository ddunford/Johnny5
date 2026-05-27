"""Consolidation ("sleep") — episodic → semantic, the growth engine (``SPEC §8``).

Consolidation is what makes Johnny *grow* rather than just accumulate logs (the
Generative-Agents reflection step; MemGPT/Letta's known gap is having *no*
automatic consolidation). It runs offline during sleep:

1. **Cluster** recent episodes by *meaning* — cosine over the existing 1024-d
   embeddings, not a group-by ``kind`` — so a theme that ran through the day
   surfaces as one cluster even when its moments were logged under different kinds.
2. **Summarise** each cluster into a durable semantic fact via the ``consolidation``
   router role (cloud-first Groq, local-qwen fallback), carrying the source-episode
   ids as provenance so a later recall can ground a thought on it.

The cluster→summarise→provenance chain is first-class, not polish: a token
summariser would defeat the point. ``parse_consolidation`` is the pure projection
(model JSON → typed summary) the contract test feeds a captured envelope through,
so a model output-shape change surfaces there, not silently in production (FC-4
house rule).

**Bounded** (the live cost guard): at most ``max_clusters`` clusters are summarised
per pass, which caps the number of cloud LLM calls a single sleep can make — the
guard while the ``consolidation`` role is cloud-first and the ``BudgetGovernor`` is
not yet a hard pre-call gate (Phase 6). Episodes past the cap fold into their
nearest existing cluster rather than spawning another call.

When every provider is tired the summariser degrades to a deterministic fallback
fact (same triple shape, low confidence) so the pass still writes provenance and
the sleep pipeline never wedges — the same graceful-degradation discipline as the
narrator/affect/deliberation agents.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from brain.config_store import ConfigStore, PromptNotFoundError, get_config_store
from brain.llm.base import LLMUnavailableError, Message
from brain.llm.router import LLMRouter
from brain.memory.base import cosine_similarity
from brain.memory.episodic import EpisodeRepository, EpisodeRow
from brain.memory.semantic import SemanticFact, SemanticMemory
from foundation.config import get_settings
from foundation.db import session_scope
from foundation.observability import get_logger

_log = get_logger("brain.memory.consolidator")

CONSOLIDATION_ROLE = "consolidation"
CONSOLIDATION_AGENT_NAME = "consolidation"

# Faithful distillation, not creative writing — a low temperature.
_TEMPERATURE = 0.4
# The fallback fact's predicate when every provider is tired (deterministic path).
_FALLBACK_PREDICATE = "recent_experience"
_FALLBACK_CONFIDENCE = 0.3
_FALLBACK_OBJECT_MAX_CHARS = 480


# ── the model response contract (pure projection, for the contract test) ───────


class ConsolidationSummary(BaseModel):
    """The semantic fact the ``consolidation`` role distils from one episode cluster.

    A subject–predicate–object triple plus a confidence in the generalisation —
    the shape ``config/prompts/consolidation.md`` asks the model for.
    """

    subject: str
    predicate: str
    object: str
    confidence: float = 0.6


def parse_consolidation(content: str) -> ConsolidationSummary:
    """Project a consolidation model response (JSON) into a ``ConsolidationSummary``.

    Pure (no I/O) so the contract test can feed a captured model envelope through
    it and assert the projection — a model output-shape change surfaces here, not
    silently in production (the house-rule contract seam, TASK-4.13).
    """
    return ConsolidationSummary.model_validate_json(content)


# ── clustering (pure, by embedding similarity) ─────────────────────────────────


@dataclass
class _Cluster:
    """A growing cluster of episodes plus the running mean of their embeddings."""

    episodes: list[EpisodeRow] = field(default_factory=list)
    _sum: list[float] = field(default_factory=list)

    def add(self, episode: EpisodeRow, vector: Sequence[float]) -> None:
        if not self._sum:
            self._sum = [float(x) for x in vector]
        else:
            self._sum = [s + float(v) for s, v in zip(self._sum, vector, strict=True)]
        self.episodes.append(episode)

    @property
    def centroid(self) -> list[float]:
        """The mean embedding — the cluster's direction for similarity tests."""
        n = len(self.episodes)
        return [s / n for s in self._sum] if n else self._sum


def cluster_episodes(
    episodes: Sequence[EpisodeRow], *, threshold: float, max_clusters: int
) -> list[list[EpisodeRow]]:
    """Greedily cluster episodes by cosine similarity over their embeddings (pure).

    Each episode joins the nearest existing cluster whose centroid it exceeds
    ``threshold`` against; otherwise it seeds a new cluster — unless ``max_clusters``
    is already reached, in which case it folds into its nearest cluster regardless
    (so the LLM-call cap holds and no episode is dropped). Returns the member lists
    in cluster-creation order.
    """
    clusters: list[_Cluster] = []
    for episode in episodes:
        vector = [float(x) for x in episode.embedding]
        best_idx, best_sim = _nearest_cluster(clusters, vector)
        # Join the nearest cluster when it's similar enough, OR when the cap is
        # reached (fold in rather than spawn another LLM call); else seed a new one.
        if best_idx is not None and (
            best_sim >= threshold or len(clusters) >= max(1, max_clusters)
        ):
            clusters[best_idx].add(episode, vector)
        else:
            new = _Cluster()
            new.add(episode, vector)
            clusters.append(new)
    return [c.episodes for c in clusters]


def _nearest_cluster(
    clusters: Sequence[_Cluster], vector: Sequence[float]
) -> tuple[int | None, float]:
    """The index + similarity of the cluster whose centroid is closest to ``vector``."""
    best_idx: int | None = None
    best_sim = -1.0
    for idx, cluster in enumerate(clusters):
        sim = cosine_similarity(vector, cluster.centroid)
        if sim > best_sim:
            best_idx, best_sim = idx, sim
    return best_idx, best_sim


# ── the consolidator ───────────────────────────────────────────────────────────


class Consolidator:
    """Cluster recent episodes and distil each cluster into a semantic fact.

    Callable, not scheduled — the sleep cycle (``brain/sleep.py``) drives it. The
    ``router`` is optional: without it (or when every provider is tired) the pass
    degrades to a deterministic fallback fact so it still writes provenance and the
    sleep pipeline never wedges.
    """

    name = CONSOLIDATION_AGENT_NAME
    model_route = CONSOLIDATION_ROLE

    def __init__(
        self,
        semantic: SemanticMemory,
        *,
        router: LLMRouter | None = None,
        config_store: ConfigStore | None = None,
        recent_limit: int | None = None,
        cluster_threshold: float | None = None,
        max_clusters: int | None = None,
        max_tokens: int | None = None,
        default_confidence: float | None = None,
    ) -> None:
        settings = get_settings()
        self._semantic = semantic
        self._router = router
        self.prompt = self._load_prompt(config_store)
        self._recent_limit = (
            recent_limit if recent_limit is not None else settings.consolidation_recent_limit
        )
        self._threshold = (
            cluster_threshold
            if cluster_threshold is not None
            else settings.consolidation_cluster_threshold
        )
        self._max_clusters = (
            max_clusters if max_clusters is not None else settings.consolidation_max_clusters
        )
        self._max_tokens = (
            max_tokens if max_tokens is not None else settings.consolidation_max_tokens
        )
        self._default_confidence = (
            default_confidence
            if default_confidence is not None
            else settings.consolidation_default_confidence
        )

    @staticmethod
    def _load_prompt(config_store: ConfigStore | None) -> str:
        try:
            return (config_store or get_config_store()).load_prompt(CONSOLIDATION_AGENT_NAME)
        except PromptNotFoundError:
            # The deterministic fallback works without a prompt; only the LLM path needs it.
            return ""

    async def run(self, *, limit: int | None = None) -> list[SemanticFact]:
        """Cluster recent episodes and write one semantic fact per cluster.

        Returns the facts written (each carrying its source episode ids). The
        number of LLM calls is bounded by ``max_clusters``. ``limit`` overrides the
        recent-episode window for this pass (the sleep cycle sets it).
        """
        cap = limit or self._recent_limit
        async with session_scope() as session:
            recent = await EpisodeRepository(session).recent(cap)
        if not recent:
            return []

        clusters = cluster_episodes(
            recent, threshold=self._threshold, max_clusters=self._max_clusters
        )
        facts: list[SemanticFact] = []
        for episodes in clusters:
            summary = await self._summarise(episodes)
            fact = await self._semantic.upsert_fact(
                subject=summary.subject,
                predicate=summary.predicate,
                obj=summary.object,
                confidence=summary.confidence,
                source_episode_ids=[e.id for e in episodes],
            )
            facts.append(fact)
        _log.info("consolidation.run.done", clusters=len(clusters), facts=len(facts))
        return facts

    async def _summarise(self, episodes: Sequence[EpisodeRow]) -> ConsolidationSummary:
        """Distil one cluster into a fact via the ``consolidation`` role; fallback when tired."""
        if self._router is not None and self.prompt:
            llm = await self._summarise_with_llm(episodes)
            if llm is not None:
                return llm
        return self._fallback_summary(episodes)

    async def _summarise_with_llm(
        self, episodes: Sequence[EpisodeRow]
    ) -> ConsolidationSummary | None:
        """Summarise a cluster through the router; ``None`` when every provider is tired."""
        assert self._router is not None
        messages = [
            Message(role="system", content=self.prompt),
            Message(role="user", content=self._render(episodes)),
        ]
        try:
            completion = await self._router.complete(
                CONSOLIDATION_ROLE,
                messages,
                schema=ConsolidationSummary,
                temperature=_TEMPERATURE,
                max_tokens=self._max_tokens,
            )
        except LLMUnavailableError:
            _log.info("consolidation.tired", cluster_size=len(episodes))
            return None
        return parse_consolidation(completion.content)

    @staticmethod
    def _render(episodes: Sequence[EpisodeRow]) -> str:
        """Format a cluster's episodes as the consolidation context."""
        fragments = "\n".join(f"- [{e.kind}] {e.content}" for e in episodes)
        return (
            "Here is a cluster of related fragments from your recent experience:\n"
            f"{fragments}\n\n"
            "Distil them into ONE durable fact, as the JSON triple "
            '{"subject": ..., "predicate": ..., "object": ..., "confidence": <0..1>}.'
        )

    def _fallback_summary(self, episodes: Sequence[EpisodeRow]) -> ConsolidationSummary:
        """A deterministic fact for the cluster when no model is available (tired).

        Keeps the same triple shape as the LLM path (so provenance + recall still
        work) at low confidence, signalling it's an unrefined distillation.
        """
        dominant_kind = _dominant_kind(episodes)
        snippets = "; ".join(e.content for e in episodes)
        return ConsolidationSummary(
            subject=f"my recent {dominant_kind}",
            predicate=_FALLBACK_PREDICATE,
            object=snippets[:_FALLBACK_OBJECT_MAX_CHARS],
            confidence=_FALLBACK_CONFIDENCE,
        )


def _dominant_kind(episodes: Sequence[EpisodeRow]) -> str:
    """The most common ``kind`` in a cluster (ties broken by first occurrence)."""
    counts: dict[str, int] = {}
    for episode in episodes:
        counts[episode.kind] = counts.get(episode.kind, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else "experience"
