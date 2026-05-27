"""``web_fetch`` — Johnny reads a public web page as clean text (``danger:network``).

A ``Tool`` on 6a's registry: ``url`` → readable text with the boilerplate
(scripts, styles, nav/footer chrome) stripped, capped so a page can't flood the
workspace (Attention is a bottleneck, ``SPEC §5``). Pairs with ``web_search`` /
``news`` (the discovery surface) and feeds the ``WebReadConsolidator`` (the read is
only "done" once it's summarised into memory).

All the danger lives in the fetch, so it delegates to ``SafeFetcher``
(``brain/effectors/safe_http.py``) — the SSRF gate (scheme allowlist, post-DNS IP
deny-list, IP-pinned connections, per-redirect re-validation, size/time caps). This
module's own job is small: validate the arg shape, run the safe fetch, and turn the
bytes (or any failure) into a typed ``ToolResult``. Operational failures — an SSRF
refusal, a timeout, a 404, a connection error — come back as a graceful
``success=False`` result (audited, heartbeat intact), never an unhandled crash.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from brain.effectors.safe_http import ALLOWED_SCHEMES, SafeFetcher, SsrfError
from brain.effectors.tools import DangerClass, ToolResult
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.effectors.web_fetch")

WEB_FETCH_TOOL_NAME = "web_fetch"

# Content types we extract text from; anything else returns a short "binary" note.
_TEXT_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})


# ── HTML → readable text (dependency-free; the summariser does the real distilling) ──

# Elements whose *content* is chrome/markup, never prose — dropped wholesale.
_SKIP_ELEMENTS = frozenset(
    {
        "script",
        "style",
        "head",
        "noscript",
        "template",
        "svg",
        "iframe",
        "object",
        "embed",
        "nav",
        "footer",
        "form",
        "button",
        "aside",
    }
)
# Elements that imply a line break around their text (so words don't run together).
_BLOCK_ELEMENTS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "br",
        "li",
        "ul",
        "ol",
        "tr",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "header",
        "main",
        "hr",
    }
)


class _ReadableTextExtractor(HTMLParser):
    """Collect visible text + the document ``<title>`` from HTML, dropping chrome.

    Not a full readability algorithm (that is the LLM consolidator's job, ``SPEC
    §8``) — a robust, dependency-free strip of scripts/styles/markup that yields
    clean prose for the summariser. ``convert_charrefs`` decodes entities for us.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._parts: list[str] = []
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_ELEMENTS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Void/self-closing tags (e.g. <br/>) — emit the break, never open a skip span.
        if tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_ELEMENTS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._skip_depth:
            return
        self._parts.append(data)

    @property
    def title(self) -> str:
        return _collapse_inline("".join(self._title_parts))

    @property
    def text(self) -> str:
        return _collapse_blocks("".join(self._parts))


def _collapse_inline(value: str) -> str:
    """Collapse runs of whitespace within a single line to one space."""
    return " ".join(value.split())


def _collapse_blocks(value: str) -> str:
    """Trim each line, drop empties, and collapse multiple blank lines to one."""
    lines = [_collapse_inline(line) for line in value.splitlines()]
    out: list[str] = []
    for line in lines:
        if line:
            out.append(line)
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip()


def extract_readable_text(html: str) -> tuple[str, str]:
    """Return ``(title, text)`` extracted from an HTML document (boilerplate stripped)."""
    parser = _ReadableTextExtractor()
    parser.feed(html)
    parser.close()
    return parser.title, parser.text


# ── the tool ──────────────────────────────────────────────────────────────────


class WebFetchArgs(BaseModel):
    """Args for ``web_fetch``: a single http(s) ``url``.

    ``extra="forbid"`` + a field validator reject a malformed/empty/non-http(s) URL
    as a *typed* ``ValidationError`` at the dispatch's vet step — before anything
    runs. (The SSRF gate re-checks the scheme on every hop regardless; this is the
    early, friendly rejection of an obviously-bad arg, not the security boundary.)
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def _looks_like_http_url(cls, value: str) -> str:
        candidate = value.strip()
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            raise ValueError("url must be an http(s) URL")
        if not parsed.hostname:
            raise ValueError("url must include a host")
        return candidate


class WebFetchTool:
    """Fetch a public URL and return its readable text (SSRF-hardened, capped)."""

    name = WEB_FETCH_TOOL_NAME
    danger = DangerClass.NETWORK
    args_schema = WebFetchArgs

    def __init__(
        self,
        *,
        fetcher: SafeFetcher | None = None,
        max_text_chars: int | None = None,
    ) -> None:
        settings = get_settings()
        self._fetcher = fetcher or SafeFetcher(
            max_redirects=settings.web_fetch_max_redirects,
            max_bytes=settings.web_fetch_max_bytes,
            timeout_seconds=settings.web_fetch_timeout_seconds,
            user_agent=settings.web_fetch_user_agent,
        )
        self._max_text_chars = (
            max_text_chars if max_text_chars is not None else settings.web_fetch_max_text_chars
        )

    async def run(self, args: WebFetchArgs) -> ToolResult:
        try:
            outcome = await self._fetcher.fetch(args.url)
        except SsrfError as exc:
            _log.info("web_fetch.blocked", url=args.url, reason=str(exc))
            return ToolResult(
                success=False,
                output={"error": "blocked", "reason": str(exc), "url": args.url},
                summary=f"refused to fetch {args.url} — {exc}",
            )
        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                output={"error": "timeout", "url": args.url},
                summary=f"timed out fetching {args.url}",
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                success=False,
                output={"error": "http_error", "reason": str(exc), "url": args.url},
                summary=f"could not fetch {args.url} — {exc}",
            )

        if outcome.status_code >= 400:
            return ToolResult(
                success=False,
                output={
                    "error": "http_status",
                    "status": outcome.status_code,
                    "url": outcome.final_url,
                },
                summary=f"{args.url} returned HTTP {outcome.status_code}",
            )

        mime = outcome.content_type.split(";", 1)[0].strip().lower()
        if mime and mime not in _TEXT_CONTENT_TYPES:
            return ToolResult(
                success=False,
                output={
                    "error": "non_text",
                    "content_type": outcome.content_type,
                    "url": outcome.final_url,
                },
                summary=f"{args.url} is non-text content ({mime}) — not read",
            )

        decoded = outcome.body.decode(_charset(outcome.content_type), errors="replace")
        if mime == "text/plain":
            title, text = "", _collapse_blocks(decoded)
        else:
            title, text = extract_readable_text(decoded)

        text, text_truncated = self._cap(text)
        return ToolResult(
            success=True,
            output={
                "url": outcome.final_url,
                "title": title,
                "text": text,
                "status": outcome.status_code,
                "content_type": outcome.content_type,
                # truncated = the raw download hit the byte cap; text_truncated = the
                # extracted text hit the char cap. Either flags "there was more".
                "truncated": outcome.truncated or text_truncated,
            },
            summary=f"read {outcome.final_url} ({len(text)} chars)"
            + (" [truncated]" if outcome.truncated or text_truncated else ""),
        )

    def _cap(self, text: str) -> tuple[str, bool]:
        """Cap extracted text to the char limit (Attention bottleneck, ``SPEC §5``)."""
        if len(text) <= self._max_text_chars:
            return text, False
        return text[: self._max_text_chars], True


def _charset(content_type: str) -> str:
    """Pull the charset from a content-type header, defaulting to UTF-8."""
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "charset" and value:
            return value.strip().strip('"').strip("'") or "utf-8"
    return "utf-8"
