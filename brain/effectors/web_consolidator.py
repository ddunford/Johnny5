"""``WebReadConsolidator`` — a web read becomes memory (the point of the curiosity loop).

Reading without remembering is wasted (``SPEC §8``): a fetched/searched page isn't
"done" until it's been distilled into Johnny's memory, so a later recall can surface
it and ground a thought. This component is that step — the bridge from the
``web_fetch``/``news`` tools to the autobiography:

1. Write an **episode** recording the read (kind ``web_read``), carrying the source
   URL — this is the provenance anchor (the autobiography *is* the web cache; no
   separate web-cache table). Only a bounded excerpt enters memory: Attention is a
   bottleneck (``SPEC §5``), so a page is distilled, never dumped whole.
2. Distil the text into a **semantic fact** via the same ``consolidation`` model
   route + ``ConsolidationSummary`` contract the Phase-4 sleep summariser uses (FC-4
   — one role, one provider chain, one budget), with its own web-read prompt. The
   fact's ``source_episode_ids`` point at the episode, so provenance chains
   fact → episode → url.

When every provider is tired it degrades to a deterministic fallback fact (same
triple shape, low confidence) so the read is *still* remembered and the loop never
wedges — the same graceful-degradation discipline as the cluster consolidator.

The curiosity loop's satisfaction fires on this consolidation (not the raw fetch):
the integration that resolves the goal + eases the drive on a successful
``consolidate`` lives with the Deliberation external-tool wiring (TASK-6b.9).
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from brain.config_store import ConfigStore, PromptNotFoundError, get_config_store
from brain.effectors.news import NEWS_TOOL_NAME
from brain.effectors.web_fetch import WEB_FETCH_TOOL_NAME
from brain.effectors.web_search import WEB_SEARCH_TOOL_NAME
from brain.llm.base import LLMUnavailableError, Message
from brain.llm.router import LLMRouter
from brain.memory.consolidator import (
    CONSOLIDATION_ROLE,
    ConsolidationSummary,
    parse_consolidation,
)
from brain.memory.episodic import Episode, EpisodicMemory
from brain.memory.semantic import SemanticFact, SemanticMemory
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.effectors.web_consolidator")

# The web-reading tools whose result is consolidated into memory (the curiosity
# loop's "remember" step). web_fetch yields a full page; web_search/news yield
# ranked results whose top item (title + snippet + url) is the thing to remember.
WEB_READ_TOOLS = frozenset({WEB_FETCH_TOOL_NAME, WEB_SEARCH_TOOL_NAME, NEWS_TOOL_NAME})

# The agent/prompt name (config/prompts/web_read_consolidation.md, FC-3 git-backed).
WEB_READ_CONSOLIDATION_AGENT = "web_read_consolidation"
# The episode kind a web read is recorded under (distinguishes it in the timeline).
WEB_READ_KIND = "web_read"

# Faithful distillation, not creative writing — match the cluster consolidator.
_TEMPERATURE = 0.4
# A page Johnny *chose* to read is fairly salient (above ambient, below a live message).
_DEFAULT_SALIENCE = 0.7
# Bound the excerpt that enters the autobiography episode (Attention bottleneck).
_EPISODE_EXCERPT_CHARS = 1500
# Bound the text handed to the summariser LLM (the rest is noise for one fact).
_SUMMARISER_INPUT_CHARS = 8000
# The tired-fallback fact's confidence + object cap (deterministic, unrefined).
_FALLBACK_CONFIDENCE = 0.3
_FALLBACK_OBJECT_CHARS = 480


class WebReadResult(BaseModel):
    """What a consolidation produced: the recorded episode + the distilled fact."""

    episode: Episode
    fact: SemanticFact
    url: str
    # True when the fact came from the LLM summariser; False on the tired fallback.
    summarised: bool


class WebReadConsolidator:
    """Distil a web read into an episode + a provenance-linked semantic fact."""

    name = WEB_READ_CONSOLIDATION_AGENT
    model_route = CONSOLIDATION_ROLE

    def __init__(
        self,
        *,
        episodic: EpisodicMemory | None = None,
        semantic: SemanticMemory | None = None,
        router: LLMRouter | None = None,
        config_store: ConfigStore | None = None,
        max_tokens: int | None = None,
        salience: float = _DEFAULT_SALIENCE,
    ) -> None:
        settings = get_settings()
        self._episodic = episodic or EpisodicMemory()
        self._semantic = semantic or SemanticMemory()
        self._router = router
        self.prompt = self._load_prompt(config_store)
        # Reuse the consolidation token ceiling (same role + provider chain, FC-4).
        self._max_tokens = (
            max_tokens if max_tokens is not None else settings.consolidation_max_tokens
        )
        self._salience = salience

    @staticmethod
    def _load_prompt(config_store: ConfigStore | None) -> str:
        try:
            return (config_store or get_config_store()).load_prompt(WEB_READ_CONSOLIDATION_AGENT)
        except PromptNotFoundError:
            # The deterministic fallback works without a prompt; only the LLM path needs it.
            return ""

    async def consolidate(self, *, url: str, title: str, text: str) -> WebReadResult:
        """Record the read as an episode + distil a fact (provenance = the url).

        Writes the episode first so the fact can carry its id as provenance; the
        fact's distillation degrades to a deterministic fallback when every provider
        is tired, so a read is always remembered.
        """
        episode = await self._episodic.write(
            Episode(
                kind=WEB_READ_KIND,
                content=self._episode_content(url=url, title=title, text=text),
                salience=self._salience,
            )
        )
        summary, summarised = await self._summarise(url=url, title=title, text=text)
        fact = await self._semantic.upsert_fact(
            subject=summary.subject,
            predicate=summary.predicate,
            obj=summary.object,
            confidence=summary.confidence,
            source_episode_ids=[episode.id] if episode.id is not None else [],
        )
        _log.info(
            "web_read.consolidated",
            url=url,
            episode_id=episode.id,
            fact=f"{fact.subject}/{fact.predicate}",
            summarised=summarised,
        )
        return WebReadResult(episode=episode, fact=fact, url=url, summarised=summarised)

    async def consolidate_tool_result(
        self, tool: str, output: Mapping[str, object]
    ) -> WebReadResult | None:
        """Consolidate a web-read tool's result, or ``None`` if there's nothing to remember.

        Knows the result shape of each web-read tool (``web_fetch`` → the page text;
        ``web_search``/``news`` → the top result's title+snippet+url) so the cycle can
        close the curiosity loop without coupling to those shapes. Returns ``None``
        when the tool isn't a web read or carried no usable content (e.g. an empty
        result set) — the caller then doesn't treat the action as a satisfied read.
        """
        extracted = _extract_web_content(tool, output)
        if extracted is None:
            return None
        url, title, text = extracted
        return await self.consolidate(url=url, title=title, text=text)

    def _episode_content(self, *, url: str, title: str, text: str) -> str:
        """The autobiography record of the read — a bounded excerpt + the url anchor."""
        excerpt = text.strip()[:_EPISODE_EXCERPT_CHARS]
        heading = title.strip() or url
        return f'I read "{heading}" ({url}).\n{excerpt}'

    async def _summarise(
        self, *, url: str, title: str, text: str
    ) -> tuple[ConsolidationSummary, bool]:
        """Distil the read into a fact via the consolidation role; tired → fallback.

        Returns ``(summary, summarised)`` where ``summarised`` is False on the
        deterministic fallback path (no router / every provider tired).
        """
        if self._router is not None and self.prompt:
            messages = [
                Message(role="system", content=self.prompt),
                Message(role="user", content=self._render(url=url, title=title, text=text)),
            ]
            try:
                completion = await self._router.complete(
                    CONSOLIDATION_ROLE,
                    messages,
                    schema=ConsolidationSummary,
                    temperature=_TEMPERATURE,
                    max_tokens=self._max_tokens,
                )
            except LLMUnavailableError:
                _log.info("web_read.summariser.tired", url=url)
            else:
                return parse_consolidation(completion.content), True
        return self._fallback_summary(url=url, title=title, text=text), False

    @staticmethod
    def _render(*, url: str, title: str, text: str) -> str:
        """Format the web read as the summariser's context (bounded input)."""
        body = text.strip()[:_SUMMARISER_INPUT_CHARS]
        return (
            f"Title: {title or '(untitled)'}\n"
            f"URL: {url}\n\n"
            f"Readable text:\n{body}\n\n"
            "Distil this into ONE durable fact, as the JSON triple "
            '{"subject": ..., "predicate": ..., "object": ..., "confidence": <0..1>}.'
        )

    def _fallback_summary(self, *, url: str, title: str, text: str) -> ConsolidationSummary:
        """A deterministic fact when no model is available (same triple shape, low confidence)."""
        subject = title.strip() or f"the page at {url}"
        return ConsolidationSummary(
            subject=subject[:200],
            predicate="says",
            object=text.strip()[:_FALLBACK_OBJECT_CHARS] or url,
            confidence=_FALLBACK_CONFIDENCE,
        )


def _extract_web_content(tool: str, output: Mapping[str, object]) -> tuple[str, str, str] | None:
    """Pull ``(url, title, text)`` to consolidate from a web-read tool's result.

    ``web_fetch`` carries the full page (needs non-empty text to be worth keeping);
    ``web_search``/``news`` carry ranked results — the top one (title+snippet+url) is
    what Johnny just "read". Returns ``None`` for a non-web tool or empty content.
    """
    if tool == WEB_FETCH_TOOL_NAME:
        url = output.get("url")
        text = output.get("text")
        if isinstance(url, str) and url and isinstance(text, str) and text.strip():
            title = output.get("title")
            return url, title if isinstance(title, str) else "", text
        return None
    if tool in (WEB_SEARCH_TOOL_NAME, NEWS_TOOL_NAME):
        results = output.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            top = results[0]
            url = top.get("url")
            if isinstance(url, str) and url:
                title = top.get("title")
                snippet = top.get("snippet")
                return (
                    url,
                    title if isinstance(title, str) else "",
                    snippet if isinstance(snippet, str) else "",
                )
        return None
    return None
