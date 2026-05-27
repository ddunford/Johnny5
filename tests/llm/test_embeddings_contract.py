"""Contract tests for the bge-m3 embeddings client.

Honours the cross-cutting rule (every response-parsing client gets a contract
test when introduced): the Embedder parses the custom :8002 /embed envelope, so
a literal captured envelope (``embed_bge_m3.json``) is projected through it and
the 1024-d guarantee + error handling are asserted. Transport behaviour uses
httpx's built-in MockTransport — no network.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

import httpx
import pytest

from brain.llm.base import ProviderError
from brain.llm.embeddings import EMBEDDINGS_PROVIDER_NAME, Embedder, parse_embeddings
from foundation.config import Settings

pytestmark = pytest.mark.contract

FixtureLoader = Callable[[str], Any]
EMBED = "llm/embed_bge_m3.json"
DIMS = 1024


def _embedder() -> Embedder:
    return Embedder(Settings())


def test_parse_projects_real_envelope_to_1024d(load_fixture: FixtureLoader) -> None:
    env = load_fixture(EMBED)
    vectors = parse_embeddings(env, expected=1, dimensions=DIMS)
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
    assert all(isinstance(x, float) for x in vectors[0])


def test_parse_count_mismatch_raises(load_fixture: FixtureLoader) -> None:
    env = load_fixture(EMBED)  # one vector
    with pytest.raises(ProviderError) as exc_info:
        parse_embeddings(env, expected=2, dimensions=DIMS)  # asked for two
    assert exc_info.value.provider == EMBEDDINGS_PROVIDER_NAME


def test_parse_dimension_mismatch_raises(load_fixture: FixtureLoader) -> None:
    env = deepcopy(load_fixture(EMBED))
    env["embeddings"][0] = env["embeddings"][0][:512]  # wrong dimensionality
    with pytest.raises(ProviderError) as exc_info:
        parse_embeddings(env, expected=1, dimensions=DIMS)
    assert exc_info.value.provider == EMBEDDINGS_PROVIDER_NAME


def test_parse_missing_embeddings_key_raises() -> None:
    with pytest.raises(ProviderError):
        parse_embeddings({"num_texts": 1}, expected=1, dimensions=DIMS)


async def test_embed_empty_input_short_circuits() -> None:
    embedder = _embedder()
    try:
        assert await embedder.embed([]) == []
    finally:
        await embedder.aclose()


async def test_embed_one_over_transport(load_fixture: FixtureLoader) -> None:
    env = load_fixture(EMBED)
    embedder = _embedder()
    await embedder.aclose()
    embedder._client = httpx.AsyncClient(
        base_url="http://test",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=env)),
    )
    try:
        vector = await embedder.embed_one("anything")
        assert len(vector) == 1024
    finally:
        await embedder.aclose()


async def test_embed_http_error_raises_provider_error() -> None:
    embedder = _embedder()
    await embedder.aclose()
    embedder._client = httpx.AsyncClient(
        base_url="http://test",
        transport=httpx.MockTransport(lambda req: httpx.Response(500, text="boom")),
    )
    try:
        with pytest.raises(ProviderError) as exc_info:
            await embedder.embed_one("anything")
        assert exc_info.value.status_code == 500
    finally:
        await embedder.aclose()
