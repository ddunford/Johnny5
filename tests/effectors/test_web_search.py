"""TC-6b.1 — the ``web_search`` tool: ranked results + graceful failure.

The SearXNG client + projection are proven in ``test_searxng.py``; here a fake
client isolates the tool's job — projecting results onto a ``ToolResult``, capping
the count, rejecting an empty query (typed), and degrading gracefully when SearXNG
is down (not a cycle crash, TC-6b.1). Pure, host-runnable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from brain.effectors.searxng import SearchResult, SearXNGError
from brain.effectors.tools import DangerClass
from brain.effectors.web_search import WEB_SEARCH_TOOL_NAME, WebSearchArgs, WebSearchTool


class _FakeClient:
    """Returns canned results (or raises ``SearXNGError``); records the call."""

    def __init__(
        self, *, results: list[SearchResult] | None = None, error: SearXNGError | None = None
    ) -> None:
        self._results = results or []
        self._error = error
        self.queries: list[str] = []

    async def search(
        self,
        query: str,
        *,
        categories: str | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> list[SearchResult]:
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return self._results


def _results(n: int) -> list[SearchResult]:
    return [
        SearchResult(
            title=f"r{i}", url=f"https://e.com/{i}", content=f"snippet {i}", engine="google"
        )
        for i in range(n)
    ]


def test_tool_declares_network_hazard() -> None:
    assert WebSearchTool.name == WEB_SEARCH_TOOL_NAME
    assert WebSearchTool.danger is DangerClass.NETWORK


async def test_search_returns_ranked_results() -> None:
    tool = WebSearchTool(client=_FakeClient(results=_results(3)))  # type: ignore[arg-type]
    result = await tool.run(WebSearchArgs(query="mars rover"))

    assert result.success is True
    assert result.output["count"] == 3
    results = result.output["results"]
    assert isinstance(results, list)
    first = results[0]
    assert first["title"] == "r0"
    assert first["url"] == "https://e.com/0"
    assert first["snippet"] == "snippet 0"


async def test_search_caps_results_to_the_limit() -> None:
    tool = WebSearchTool(client=_FakeClient(results=_results(20)), max_results=5)  # type: ignore[arg-type]
    result = await tool.run(WebSearchArgs(query="x"))
    assert result.output["count"] == 5


async def test_explicit_limit_is_clamped_to_the_max() -> None:
    tool = WebSearchTool(client=_FakeClient(results=_results(20)), max_results=5)  # type: ignore[arg-type]
    result = await tool.run(WebSearchArgs(query="x", limit=25))
    assert result.output["count"] == 5  # tool max wins over a larger requested limit


async def test_searxng_down_is_a_graceful_failure() -> None:
    tool = WebSearchTool(client=_FakeClient(error=SearXNGError("down")))  # type: ignore[arg-type]
    result = await tool.run(WebSearchArgs(query="x"))
    assert result.success is False
    assert result.output["error"] == "search_failed"


async def test_empty_results_is_success_with_zero() -> None:
    tool = WebSearchTool(client=_FakeClient(results=[]))  # type: ignore[arg-type]
    result = await tool.run(WebSearchArgs(query="zxqv"))
    assert result.success is True
    assert result.output["count"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({}, id="missing-query"),
        pytest.param({"query": ""}, id="empty-query"),
        pytest.param({"query": "x", "limit": 0}, id="limit-too-small"),
        pytest.param({"query": "x", "limit": 99}, id="limit-too-large"),
        pytest.param({"query": "x", "extra": "no"}, id="unknown-field"),
    ],
)
def test_bad_args_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WebSearchArgs.model_validate(bad)
