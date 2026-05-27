"""Deterministic, seeded embeddings for memory tests.

Recall-ranking tests must be **reproducible**. If they embedded through the live
TEI bge-m3 server, the cosine ordering would drift with the model version and
the network, and a "does hybrid recall beat pure similarity?" assertion would be
non-deterministic. Instead we inject *known* unit vectors so similarity is exact
and fully under the test's control (the carried-over guidance for TC-1.2).

Primitives
----------
* ``axis_vector(index)`` — a one-hot unit vector on a single axis. Distinct
  indices are exactly orthogonal (cosine 0.0); the same index is identical
  (cosine 1.0). This is what lets a test make two episodes *tie* on pure
  similarity so that only the recency+salience blend can separate them.
* ``seeded_vector(seed)`` — a deterministic pseudo-random unit vector. Two
  distinct seeds are near-orthogonal in 1024-d (independent Gaussian directions
  concentrate around cosine 0), standing in for "unrelated" content without
  hand-picking an axis.
* ``perturbed(vec, seed, strength)`` — a near-copy of ``vec`` (high but < 1.0
  cosine) for cases that need "very similar but not identical".
* ``cosine_similarity(a, b)`` — for sanity assertions inside tests.

``DeterministicEmbedder`` mirrors the async surface of
``brain.llm.embeddings.Embedder`` (``embed`` / ``embed_one`` / ``aclose``) so it
can be injected wherever the real embedder is consumed. It resolves each text
through a caller-supplied mapping (or resolver); unknown text falls back to a
seeded vector derived from the text itself, so it is stable across runs. Every
batch it is asked to embed is recorded on ``.calls`` — a test can assert that
recall actually embedded its query *through the embedder* (never inline).
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable, Mapping, Sequence

# Matches ``Settings.embed_dimensions`` (bge-m3, 1024-d). Kept as a module
# constant so tests don't hard-code the magic number all over the place.
DIMENSIONS = 1024


def _normalise(vec: list[float]) -> list[float]:
    """Scale ``vec`` to unit length (so cosine similarity == dot product)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        raise ValueError("cannot normalise a zero vector")
    return [x / norm for x in vec]


def _seed_to_int(seed: object) -> int:
    """Map any seed (int or otherwise) to a stable integer for ``random.Random``."""
    if isinstance(seed, int):
        return seed
    digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def axis_vector(index: int, *, dimensions: int = DIMENSIONS) -> list[float]:
    """A one-hot unit vector with ``1.0`` on ``index`` and ``0.0`` elsewhere.

    Distinct indices are exactly orthogonal; identical indices are identical.
    The obvious tool for building "same topic" vs "unrelated" episodes whose
    similarity ordering is unambiguous.
    """
    if not 0 <= index < dimensions:
        raise ValueError(f"axis index {index} out of range for {dimensions}-d vector")
    vec = [0.0] * dimensions
    vec[index] = 1.0
    return vec


def seeded_vector(seed: object, *, dimensions: int = DIMENSIONS) -> list[float]:
    """A deterministic pseudo-random unit vector for the given ``seed``.

    Components are drawn i.i.d. Gaussian then normalised, giving a uniform
    direction on the unit sphere. The same seed always yields the same vector;
    distinct seeds are near-orthogonal in high dimension.
    """
    rng = random.Random(_seed_to_int(seed))
    return _normalise([rng.gauss(0.0, 1.0) for _ in range(dimensions)])


def perturbed(
    vec: Sequence[float], *, seed: object = "perturb", strength: float = 0.05
) -> list[float]:
    """A near-copy of ``vec``: a unit vector with high (< 1.0) cosine to it.

    ``strength`` is the relative magnitude of the orthogonal-ish noise added
    before re-normalising — small values stay very close to the original.
    """
    if strength < 0:
        raise ValueError("strength must be non-negative")
    base = list(vec)
    noise = seeded_vector(seed, dimensions=len(base))
    return _normalise([b + strength * n for b, n in zip(base, noise, strict=True)])


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors (for test assertions)."""
    if len(a) != len(b):
        raise ValueError("vectors must have the same length")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        raise ValueError("cannot take cosine similarity of a zero vector")
    return dot / (norm_a * norm_b)


class DeterministicEmbedder:
    """Drop-in stand-in for ``brain.llm.embeddings.Embedder`` with known vectors.

    Resolution order for a text: explicit ``mapping`` → ``resolver`` callable →
    a ``seeded_vector`` derived from the text. Every embedded batch is appended
    to ``.calls`` so a test can prove recall embedded its query through here.
    """

    def __init__(
        self,
        mapping: Mapping[str, Sequence[float]] | None = None,
        *,
        resolver: Callable[[str], Sequence[float]] | None = None,
        dimensions: int = DIMENSIONS,
    ) -> None:
        self._mapping = {k: list(v) for k, v in (mapping or {}).items()}
        self._resolver = resolver
        self._dimensions = dimensions
        self.calls: list[list[str]] = []

    def set(self, text: str, vector: Sequence[float]) -> None:
        """Pin ``text`` to ``vector`` (overrides resolver/fallback for that text)."""
        self._mapping[text] = list(vector)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        batch = list(texts)
        self.calls.append(batch)
        return [self._resolve(text) for text in batch]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]

    async def aclose(self) -> None:
        """No-op: nothing to close (kept for interface parity with ``Embedder``)."""
        return None

    def _resolve(self, text: str) -> list[float]:
        if text in self._mapping:
            vector: Sequence[float] = self._mapping[text]
        elif self._resolver is not None:
            vector = self._resolver(text)
        else:
            vector = seeded_vector(text, dimensions=self._dimensions)
        out = [float(x) for x in vector]
        if len(out) != self._dimensions:
            raise ValueError(
                f"resolved a {len(out)}-d vector for {text!r}; expected {self._dimensions}-d"
            )
        return out
