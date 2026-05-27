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
from datetime import datetime
from typing import Protocol, runtime_checkable

from foundation.config import Settings


@runtime_checkable
class EmbeddingClient(Protocol):
    """What the memory stores need from an embedder (Phase 0 ``Embedder`` satisfies it)."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]: ...


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
