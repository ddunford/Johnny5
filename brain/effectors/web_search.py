"""``web_search`` — Johnny searches the web (``danger:network``), his discovery surface.

A query → ranked results (title / url / snippet) via SearXNG. This is how an idle,
curious Johnny finds *what* to read; ``web_fetch`` then reads a chosen result and
``WebReadConsolidator`` remembers it — the curiosity loop. A ``Tool`` on 6a's belt,
so it's Conscience-vetted + budget-bounded + audited automatically.

Thin over ``SearXNGClient``: validate the query, run the search (capped to keep the
workspace bounded — Attention is a bottleneck), and project to a ``ToolResult``. A
flaky/down SearXNG comes back as a graceful ``success=False`` result, never a cycle
crash (TC-6b.1); an empty query is a typed arg-validation error.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from brain.effectors.searxng import (
    CATEGORY_GENERAL,
    SearXNGClient,
    SearXNGError,
    results_to_payload,
)
from brain.effectors.tools import DangerClass, ToolResult
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.effectors.web_search")

WEB_SEARCH_TOOL_NAME = "web_search"


class WebSearchArgs(BaseModel):
    """Args for ``web_search``: a non-empty ``query`` (+ optional result ``limit``)."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=512)
    limit: int | None = Field(default=None, ge=1, le=25)


class WebSearchTool:
    """Search the web via SearXNG → ranked results (``danger:network``)."""

    name = WEB_SEARCH_TOOL_NAME
    danger = DangerClass.NETWORK
    args_schema = WebSearchArgs

    def __init__(
        self,
        *,
        client: SearXNGClient | None = None,
        max_results: int | None = None,
    ) -> None:
        settings = get_settings()
        self._client = client or SearXNGClient()
        self._max_results = (
            max_results if max_results is not None else settings.web_search_max_results
        )

    async def run(self, args: WebSearchArgs) -> ToolResult:
        limit = min(args.limit or self._max_results, self._max_results)
        try:
            results = await self._client.search(args.query, categories=CATEGORY_GENERAL)
        except SearXNGError as exc:
            _log.info("web_search.failed", query=args.query, reason=str(exc))
            return ToolResult(
                success=False,
                output={"error": "search_failed", "reason": str(exc), "query": args.query},
                summary=f"web search for {args.query!r} failed — {exc}",
            )

        payload = results_to_payload(results, limit=limit)
        return ToolResult(
            success=True,
            output={"query": args.query, "results": payload, "count": len(payload)},
            summary=f"searched {args.query!r} — {len(payload)} result(s)",
        )
