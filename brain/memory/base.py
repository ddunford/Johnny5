"""Shared memory primitives: the embedding contract, recall weights, scoring.

The stores depend on the *structural* ``EmbeddingClient`` protocol rather than
the concrete Phase 0 ``Embedder`` so tests can inject deterministic embeddings
(the recall-ranking tests need fixed vectors). The scoring helpers implement the
hybrid recall blend — vector similarity plus recency and salience — each
component normalised to ``[0, 1]`` and combined as a weighted sum (the formula in
``SPEC §8`` / the Generative Agents pattern). Weights come from settings.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from foundation.config import Settings, get_settings


@runtime_checkable
class EmbeddingClient(Protocol):
    """What the memory stores need from an embedder (Phase 0 ``Embedder`` satisfies it)."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]: ...


def default_embedder() -> EmbeddingClient:
    """The production embedder (TEI BGE-M3 on ``:8002``) the stores use when none
    is injected. Tests inject a deterministic ``EmbeddingClient`` instead, so
    recall ranking is reproducible. Imported lazily to avoid pulling the LLM
    layer into modules that only need the protocol."""
    from brain.llm.embeddings import Embedder

    return Embedder(get_settings())


def utcnow() -> datetime:
    """Default wall clock for recency. Injected (``now_fn``) so tests can freeze it."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class RecallWeights:
    """Weights for the episodic recall blend, plus the recency half-life.

    Equal defaults mean similarity, recency, and salience contribute alike;
    runtime tuning (Phase 3/4) changes these without a code edit.
    """

    similarity: float = 1.0
    recency: float = 1.0
    salience: float = 1.0
    recency_halflife_seconds: float = 86400.0

    @classmethod
    def from_settings(cls, settings: Settings) -> RecallWeights:
        return cls(
            similarity=settings.memory_recall_weight_similarity,
            recency=settings.memory_recall_weight_recency,
            salience=settings.memory_recall_weight_salience,
            recency_halflife_seconds=settings.memory_recall_recency_halflife_seconds,
        )


def clamp01(value: float) -> float:
    """Clamp to ``[0, 1]``."""
    return min(1.0, max(0.0, value))


def similarity_from_distance(distance: float) -> float:
    """Map pgvector cosine distance (``[0, 2]``) to a ``[0, 1]`` similarity."""
    return clamp01(1.0 - distance)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors, in ``[-1, 1]``.

    The in-memory counterpart to pgvector's ``cosine_distance`` — used where the
    embeddings are already loaded and a round-trip to the ANN index would be
    wasteful (consolidation clustering, semantic dedupe). A zero-norm vector has
    no direction, so similarity is defined as ``0.0`` rather than raising.
    """
    if len(a) != len(b):
        raise ValueError("vectors must have the same length")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def recency_score(ts: datetime, now: datetime, halflife_seconds: float) -> float:
    """Exponential time-decay in ``[0, 1]``: 1.0 at ``now``, 0.5 at one half-life old."""
    if halflife_seconds <= 0:
        return 1.0
    age_seconds = max(0.0, (now - ts).total_seconds())
    return math.exp(-math.log(2.0) * age_seconds / halflife_seconds)


def blended_score(
    *, similarity: float, recency: float, salience: float, weights: RecallWeights
) -> float:
    """The hybrid relevance: ``w_sim·sim + w_rec·recency + w_sal·salience``."""
    return weights.similarity * similarity + weights.recency * recency + weights.salience * salience
