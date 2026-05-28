"""TC-6b.1 (contract + client) — SearXNG projection + the httpx client.

Two layers, both deterministic (no real network):
* ``parse_search_results`` is fed the **captured** SearXNG envelope
  (``tests/fixtures/searxng/search.json``, recorded live off ``inference.lan``) and
  must project it — a SearXNG shape change breaks this test, not production.
* ``SearXNGClient`` is driven through an ``httpx.MockTransport`` to prove it sends
  the verified contract (``format=json`` + curated ``engines``), parses a 200, and
  turns a down/500/bad-JSON SearXNG into a graceful ``SearXNGError`` (never a hang).
Host-runnable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from brain.effectors.searxng import (
    SearXNGClient,
    SearXNGError,
    parse_search_results,
)

# ── contract: project the captured envelope ───────────────────────────────────


def test_parse_projects_the_captured_envelope(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("searxng/search.json")
    results = parse_search_results(payload)

    assert len(results) == len(payload["results"])
    first = results[0]
    raw_first = payload["results"][0]
    assert first.title == raw_first["title"]
    assert first.url == raw_first["url"]
    assert first.content == raw_first["content"]
    assert first.engine == raw_first["engine"]


def test_parse_empty_envelope_is_empty(load_fixture: Callable[[str], Any]) -> None:
    assert parse_search_results(load_fixture("searxng/search.empty.json")) == []


def test_parse_projects_the_news_envelope(load_fixture: Callable[[str], Any]) -> None:
    # The captured news envelope carries publishedDate — the projection keeps it.
    results = parse_search_results(load_fixture("searxng/news.json"))
    assert results
    assert any(r.published_date for r in results)


def test_parse_skips_results_without_a_url() -> None:
    payload: dict[str, object] = {
        "results": [{"title": "no url"}, {"url": "https://x.com", "title": "ok"}]
    }
    results = parse_search_results(payload)
    assert [r.url for r in results] == ["https://x.com"]


def test_parse_tolerates_a_missing_results_key() -> None:
    payload: dict[str, object] = {"query": "x"}
    assert parse_search_results(payload) == []


# ── the client: real parse path over a mocked transport ────────────────────────


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> SearXNGClient:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return SearXNGClient(client_factory=factory, engines="google,bing,brave")


async def test_client_sends_the_verified_contract(load_fixture: Callable[[str], Any]) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=load_fixture("searxng/search.json"))

    results = await _client(handler).search("mars rover", categories="general")

    assert seen["format"] == "json"  # JSON contract
    assert seen["engines"] == "google,bing,brave"  # curated set (NOT duckduckgo)
    assert seen["categories"] == "general"
    assert seen["q"] == "mars rover"
    assert len(results) >= 1


async def test_client_omits_engines_when_none() -> None:
    # News scopes to the category's own engines — forcing the general set pollutes it.
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"results": []})

    await _client(handler).search("mars", categories="news", engines=None)

    assert "engines" not in seen  # omitted
    assert seen["categories"] == "news"


async def test_client_raises_searxng_error_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream boom")

    with pytest.raises(SearXNGError):
        await _client(handler).search("anything")


async def test_client_raises_searxng_error_on_bad_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(SearXNGError):
        await _client(handler).search("anything")


async def test_client_raises_searxng_error_when_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SearXNGError):
        await _client(handler).search("anything")


def test_search_json_fixture_is_real_shaped(load_fixture: Callable[[str], Any]) -> None:
    # Guards that the captured fixture keeps the keys the projection depends on.
    payload = load_fixture("searxng/search.json")
    assert {"query", "number_of_results", "results"} <= set(payload)
    assert {"url", "title", "content", "engine"} <= set(payload["results"][0])
    # Round-trips as JSON (it's a real captured wire body).
    json.dumps(payload)


# ── @live: the real SearXNG round-trip (skipped unless --live) ─────────────────


@pytest.mark.live
async def test_live_searxng_round_trip() -> None:
    """Hit the real SearXNG on inference.lan with the curated engines (the verified
    contract) and confirm it returns usable results — the real-network proof behind
    the deterministic fixture. Run with ``--live``."""
    results = await SearXNGClient().search("mars rover", categories="general")
    assert results, "curated-engine query should return hits from the real box"
    assert all(r.url for r in results)
    assert any(r.title for r in results)


@pytest.mark.live
async def test_live_searxng_news_round_trip() -> None:
    """Real news-category round-trip (no forced engines) — returns dated items. --live."""
    results = await SearXNGClient().search("mars", categories="news", engines=None)
    assert results, "news category should return items from the real box"
    assert any(r.published_date for r in results), "news results should carry publishedDate"
