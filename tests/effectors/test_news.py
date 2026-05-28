"""TC-6b.3 — the ``news`` tool: recent items by topic, newest-first, graceful.

The SearXNG client + projection are proven in ``test_searxng.py``; here a fake
client isolates the tool's job — sorting newest-first (dated before undated),
projecting to a ``ToolResult``, capping the count, rejecting an empty topic, and
degrading gracefully when SearXNG is down. Pure, host-runnable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from brain.effectors.news import NEWS_TOOL_NAME, NewsArgs, NewsTool
from brain.effectors.searxng import SearchResult, SearXNGError
from brain.effectors.tools import DangerClass


class _FakeClient:
    """Returns canned results (or raises); records the category/engines it was asked for."""

    def __init__(
        self, *, results: list[SearchResult] | None = None, error: SearXNGError | None = None
    ) -> None:
        self._results = results or []
        self._error = error
        self.categories: str | None = None
        self.engines_omitted = False

    async def search(
        self,
        query: str,
        *,
        categories: str | None = None,
        engines: object = object(),
        extra_params: dict[str, str] | None = None,
    ) -> list[SearchResult]:
        self.categories = categories
        self.engines_omitted = engines is None
        if self._error is not None:
            raise self._error
        return self._results


def test_tool_declares_network_hazard() -> None:
    assert NewsTool.name == NEWS_TOOL_NAME
    assert NewsTool.danger is DangerClass.NETWORK


async def test_news_uses_the_news_category_without_forced_engines() -> None:
    client = _FakeClient(results=[])
    await NewsTool(client=client).run(NewsArgs(topic="mars"))  # type: ignore[arg-type]
    assert client.categories == "news"
    assert client.engines_omitted is True  # the verified contract (news scopes its own engines)


async def test_news_sorts_newest_first_dated_before_undated() -> None:
    results = [
        SearchResult(
            title="old", url="https://e.com/old", published_date="2020-01-01T00:00:00+00:00"
        ),
        SearchResult(title="undated", url="https://e.com/u"),
        SearchResult(
            title="new", url="https://e.com/new", published_date="2026-05-01T00:00:00+00:00"
        ),
    ]
    result = await NewsTool(client=_FakeClient(results=results)).run(NewsArgs(topic="mars"))  # type: ignore[arg-type]

    rows = result.output["results"]
    assert isinstance(rows, list)
    titles = [r["title"] for r in rows]
    assert titles == ["new", "old", "undated"]  # newest dated, then older dated, then undated


async def test_news_caps_results() -> None:
    many = [SearchResult(title=f"n{i}", url=f"https://e.com/{i}") for i in range(20)]
    result = await NewsTool(client=_FakeClient(results=many), max_results=4).run(  # type: ignore[arg-type]
        NewsArgs(topic="mars")
    )
    assert result.output["count"] == 4


async def test_news_down_is_graceful() -> None:
    tool = NewsTool(client=_FakeClient(error=SearXNGError("down")))  # type: ignore[arg-type]
    result = await tool.run(NewsArgs(topic="mars"))
    assert result.success is False
    assert result.output["error"] == "news_failed"


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({}, id="missing-topic"),
        pytest.param({"topic": ""}, id="empty-topic"),
        pytest.param({"topic": "x", "limit": 0}, id="limit-too-small"),
        pytest.param({"topic": "x", "extra": "no"}, id="unknown-field"),
    ],
)
def test_bad_args_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NewsArgs.model_validate(bad)
