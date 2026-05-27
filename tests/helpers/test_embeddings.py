"""Tests for the deterministic-embedding helper itself.

The recall-ranking tests (TC-1.2) lean entirely on these vectors behaving
predictably — same topic identical, different topics orthogonal, seeds stable
and reproducible. If that contract slips, the ranking assertions become
meaningless, so the helper gets its own coverage (same convention as
``test_clock.py``).
"""

from __future__ import annotations

import math

import pytest

from helpers.embeddings import (
    DIMENSIONS,
    DeterministicEmbedder,
    axis_vector,
    cosine_similarity,
    perturbed,
    seeded_vector,
)


def _is_unit(vec: list[float]) -> bool:
    return math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, abs_tol=1e-9)


# ── axis_vector ──────────────────────────────────────────────────────────────


def test_axis_vector_is_unit_length_and_one_hot() -> None:
    vec = axis_vector(7)
    assert len(vec) == DIMENSIONS
    assert _is_unit(vec)
    assert vec[7] == 1.0
    assert sum(1 for x in vec if x != 0.0) == 1


def test_same_axis_is_identical_distinct_axes_orthogonal() -> None:
    # Two episodes on the same topic tie perfectly on similarity ...
    assert cosine_similarity(axis_vector(3), axis_vector(3)) == pytest.approx(1.0)
    # ... while an unrelated topic is exactly orthogonal (excluded by similarity).
    assert cosine_similarity(axis_vector(3), axis_vector(4)) == pytest.approx(0.0)


def test_axis_vector_rejects_out_of_range_index() -> None:
    with pytest.raises(ValueError):
        axis_vector(DIMENSIONS)
    with pytest.raises(ValueError):
        axis_vector(-1)


# ── seeded_vector ────────────────────────────────────────────────────────────


def test_seeded_vector_is_deterministic_and_unit_length() -> None:
    a = seeded_vector("connection")
    b = seeded_vector("connection")
    assert a == b
    assert len(a) == DIMENSIONS
    assert _is_unit(a)


def test_seeded_vector_int_and_str_seeds_supported() -> None:
    assert seeded_vector(42) == seeded_vector(42)
    assert seeded_vector(42) != seeded_vector(43)


def test_distinct_seeds_are_near_orthogonal_in_high_dim() -> None:
    # Independent directions in 1024-d concentrate around cosine 0; well under a
    # threshold that could ever flip a recall ranking.
    sim = cosine_similarity(seeded_vector("apples"), seeded_vector("orbital mechanics"))
    assert abs(sim) < 0.2


# ── perturbed ────────────────────────────────────────────────────────────────


def test_perturbed_is_close_but_not_identical() -> None:
    base = axis_vector(0)
    near = perturbed(base, strength=0.05)
    assert _is_unit(near)
    sim = cosine_similarity(base, near)
    assert 0.9 < sim < 1.0


def test_perturbed_strength_controls_closeness() -> None:
    base = seeded_vector("base")
    gentle = cosine_similarity(base, perturbed(base, strength=0.02))
    rough = cosine_similarity(base, perturbed(base, strength=0.30))
    assert gentle > rough


# ── DeterministicEmbedder ────────────────────────────────────────────────────


async def test_embedder_resolves_from_mapping() -> None:
    embedder = DeterministicEmbedder({"hello": axis_vector(0)})
    assert await embedder.embed_one("hello") == axis_vector(0)


async def test_embedder_falls_back_to_stable_seeded_vector() -> None:
    embedder = DeterministicEmbedder()
    first = await embedder.embed_one("unmapped text")
    second = await embedder.embed_one("unmapped text")
    assert first == second == seeded_vector("unmapped text")


async def test_embedder_uses_resolver_when_provided() -> None:
    embedder = DeterministicEmbedder(resolver=lambda _text: axis_vector(5))
    assert await embedder.embed_one("anything") == axis_vector(5)


async def test_embedder_records_every_batch() -> None:
    embedder = DeterministicEmbedder()
    await embedder.embed(["a", "b"])
    await embedder.embed_one("c")
    assert embedder.calls == [["a", "b"], ["c"]]


async def test_embedder_set_overrides_resolution() -> None:
    embedder = DeterministicEmbedder()
    embedder.set("pinned", axis_vector(9))
    assert await embedder.embed_one("pinned") == axis_vector(9)


async def test_embedder_rejects_wrong_dimensionality() -> None:
    embedder = DeterministicEmbedder({"bad": [1.0, 0.0, 0.0]})
    with pytest.raises(ValueError):
        await embedder.embed_one("bad")


async def test_embedder_preserves_batch_order() -> None:
    embedder = DeterministicEmbedder({"first": axis_vector(0), "second": axis_vector(1)})
    vectors = await embedder.embed(["second", "first"])
    assert vectors == [axis_vector(1), axis_vector(0)]
