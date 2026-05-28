"""``news`` — Johnny browses recent news by topic (``danger:network``).

The **primary curiosity feed** (``SPEC §9.1``): an idle, curious Johnny browses
news on a topic, picks something, reads it (``web_fetch``), and remembers it
(``WebReadConsolidator``). A ``Tool`` on 6a's belt → Conscience-vetted + audited.

Backed by the same ``SearXNGClient`` as ``web_search`` but with the **news**
category and — verified live on the box — **no forced engine set**: the news
category supplies its own fast engines, whereas forcing the general
``google,bing,brave`` set pollutes the feed with non-news hits. Results carry a
``published_date`` for most items, so the tool sorts newest-first (dated before
undated) — "browse by recency". A flaky/down SearXNG degrades to a graceful
``success=False`` result, never a cycle crash.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from brain.effectors.searxng import (
    CATEGORY_NEWS,
    SearchResult,
    SearXNGClient,
    SearXNGError,
    results_to_payload,
)
from brain.effectors.tools import DangerClass, ToolResult
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.effectors.news")

NEWS_TOOL_NAME = "news"


class NewsArgs(BaseModel):
    """Args for ``news``: a ``topic`` to browse (+ optional result ``limit``)."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=512)
    limit: int | None = Field(default=None, ge=1, le=25)


def _recency_key(result: SearchResult) -> tuple[int, str]:
    """Sort key: dated items first, newest ``published_date`` (ISO) first."""
    if result.published_date:
        return (1, result.published_date)
    return (0, "")


class NewsTool:
    """Browse recent news on a topic via SearXNG (``danger:network``)."""

    name = NEWS_TOOL_NAME
    danger = DangerClass.NETWORK
    args_schema = NewsArgs

    def __init__(
        self,
        *,
        client: SearXNGClient | None = None,
        max_results: int | None = None,
    ) -> None:
        settings = get_settings()
        self._client = client or SearXNGClient()
        self._max_results = max_results if max_results is not None else settings.news_max_results

    async def run(self, args: NewsArgs) -> ToolResult:
        limit = min(args.limit or self._max_results, self._max_results)
        try:
            # No forced engines (engines=None): the news category uses its own; the
            # general set would pollute the feed. (Verified live on inference.lan.)
            results = await self._client.search(args.topic, categories=CATEGORY_NEWS, engines=None)
        except SearXNGError as exc:
            _log.info("news.failed", topic=args.topic, reason=str(exc))
            return ToolResult(
                success=False,
                output={"error": "news_failed", "reason": str(exc), "topic": args.topic},
                summary=f"news browse for {args.topic!r} failed — {exc}",
            )

        ranked = sorted(results, key=_recency_key, reverse=True)
        payload = results_to_payload(ranked, limit=limit)
        return ToolResult(
            success=True,
            output={"topic": args.topic, "results": payload, "count": len(payload)},
            summary=f"browsed news on {args.topic!r} — {len(payload)} item(s)",
        )
