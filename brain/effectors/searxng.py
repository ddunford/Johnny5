"""SearXNG client + the projection from its JSON envelope to typed results.

The shared backing for Johnny's discovery surface — ``web_search`` (this module)
and ``news`` (``brain/effectors/news.py``) both query the self-hosted SearXNG on
``inference.lan`` through ``SearXNGClient``. SearXNG is *trusted internal infra*
(not an arbitrary URL Johnny chose), so it does NOT go through the SSRF gate that
``web_fetch`` does — that gate is for the open web Johnny then reads.

Two verified facts about the box drive the defaults (lessons.md — verified live,
not assumed): the JSON ``format`` must be explicitly requested, and the engine set
**must** be curated — ``google,bing,brave`` return results; ``duckduckgo`` returns
zero and an empty engine set times out. So ``engines=`` is always sent.

``parse_search_results`` is the pure projection from the envelope to typed
``SearchResult``s — the house-rule contract seam, pinned by a captured-envelope
fixture (``tests/fixtures/searxng/search.json``), so a SearXNG shape change surfaces
in a test rather than silently in production. The httpx client is injectable so the
deterministic suite stubs it with ``httpx.MockTransport`` (no real network).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx
from pydantic import BaseModel

from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.effectors.searxng")

# The general-search category (web_search); news uses its own (see news.py).
CATEGORY_GENERAL = "general"


class SearchResult(BaseModel):
    """One ranked result, projected from a SearXNG ``results[]`` entry.

    ``content`` is SearXNG's snippet; ``published_date`` is populated for news-ish
    results and ``None`` for general web hits.
    """

    title: str
    url: str
    content: str = ""
    engine: str = ""
    score: float | None = None
    published_date: str | None = None


def parse_search_results(payload: dict[str, object]) -> list[SearchResult]:
    """Project a SearXNG JSON envelope's ``results`` into typed ``SearchResult``s (pure).

    Tolerant of missing/extra keys (SearXNG results vary by engine): only ``url`` is
    required for a usable result; a row without one is skipped rather than raising.
    """
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return []
    out: list[SearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url:
            continue
        out.append(
            SearchResult(
                title=_as_str(item.get("title")),
                url=url,
                content=_as_str(item.get("content")),
                engine=_as_str(item.get("engine")),
                score=_as_float(item.get("score")),
                published_date=_as_opt_str(item.get("publishedDate")),
            )
        )
    return out


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class SearXNGError(Exception):
    """A SearXNG query could not be completed (down, timeout, or a non-200 reply).

    Caught by the calling tool and turned into a graceful failed ``ToolResult`` — a
    flaky search must never crash the cognitive cycle (TC-6b.1).
    """


ClientFactory = Callable[[], httpx.AsyncClient]


class SearXNGClient:
    """Query the self-hosted SearXNG JSON API (engines curated, timeout-bounded)."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        engines: str | None = None,
        timeout_seconds: float | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.searxng_url).rstrip("/")
        self._engines = engines if engines is not None else settings.searxng_engines
        self._timeout = (
            timeout_seconds if timeout_seconds is not None else settings.searxng_timeout_seconds
        )
        self._client_factory = client_factory

    def _new_client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=self._timeout)

    async def search(
        self,
        query: str,
        *,
        categories: str | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> list[SearchResult]:
        """Run a query and return the typed results; raise ``SearXNGError`` on failure.

        Always sends ``format=json`` + the curated ``engines`` (the verified contract).
        ``categories`` (e.g. ``news``) and ``extra_params`` (e.g. ``time_range``) are
        optional refinements.
        """
        params: dict[str, str] = {
            "q": query,
            "format": "json",
            "engines": self._engines,
        }
        if categories:
            params["categories"] = categories
        if extra_params:
            params.update(extra_params)

        try:
            async with self._new_client() as client:
                response = await client.get(f"{self._base_url}/search", params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:  # network error or bad JSON
            raise SearXNGError(f"SearXNG query failed: {exc}") from exc

        if not isinstance(payload, dict):
            raise SearXNGError("SearXNG returned a non-object JSON body")
        results = parse_search_results(payload)
        _log.info("searxng.search", query=query, categories=categories, results=len(results))
        return results


def results_to_payload(results: Sequence[SearchResult], *, limit: int) -> list[dict[str, object]]:
    """A compact, capped serialisation of results for a ``ToolResult`` (Attention bound)."""
    return [
        {
            "title": r.title,
            "url": r.url,
            "snippet": r.content,
            "engine": r.engine,
            "published_date": r.published_date,
        }
        for r in results[:limit]
    ]
