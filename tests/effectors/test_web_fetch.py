"""TC-6b.2 — the ``web_fetch`` tool: clean-text extraction + graceful failures.

The SSRF gate is proven separately (``test_safe_http.py``); here we inject a fake
fetcher so the tool's own job is under test: strip boilerplate to readable text,
cap it, validate the arg shape, and turn every failure mode (SSRF refusal, timeout,
404, non-text) into a graceful ``success=False`` result rather than a crash. Pure,
no DB/Redis.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from brain.effectors.safe_http import FetchOutcome, SsrfError
from brain.effectors.tools import DangerClass
from brain.effectors.web_fetch import (
    WEB_FETCH_TOOL_NAME,
    WebFetchArgs,
    WebFetchTool,
    extract_readable_text,
)

# ── HTML → readable text ──────────────────────────────────────────────────────


def test_extraction_strips_scripts_styles_and_chrome() -> None:
    html = """
    <html><head><title>  My  Page </title>
    <style>.x{color:red}</style><script>alert(1)</script></head>
    <body><nav>home about</nav>
    <h1>Heading</h1><p>First   paragraph.</p>
    <p>Second paragraph.</p>
    <footer>copyright</footer></body></html>
    """
    title, text = extract_readable_text(html)

    assert title == "My Page"  # collapsed whitespace
    assert "Heading" in text
    assert "First paragraph." in text
    assert "Second paragraph." in text
    # boilerplate is gone
    assert "alert(1)" not in text
    assert "color:red" not in text
    assert "home about" not in text
    assert "copyright" not in text


def test_extraction_keeps_block_text_on_separate_lines() -> None:
    _, text = extract_readable_text("<p>one</p><p>two</p>")
    assert "one" in text and "two" in text
    assert "onetwo" not in text  # blocks don't run together


# ── arg validation (typed rejection at the dispatch's vet step) ───────────────


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({}, id="missing-url"),
        pytest.param({"url": ""}, id="empty-url"),
        pytest.param({"url": "file:///etc/passwd"}, id="non-http-scheme"),
        pytest.param({"url": "gopher://x/"}, id="gopher-scheme"),
        pytest.param({"url": "not a url"}, id="no-host"),
        pytest.param({"url": "http://x/", "extra": "no"}, id="unknown-field"),
    ],
)
def test_bad_args_raise_validation_error(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WebFetchArgs.model_validate(bad)


def test_valid_url_is_accepted_and_trimmed() -> None:
    args = WebFetchArgs.model_validate({"url": "  https://example.com/x  "})
    assert args.url == "https://example.com/x"


def test_tool_declares_network_hazard() -> None:
    assert WebFetchTool.name == WEB_FETCH_TOOL_NAME
    assert WebFetchTool.danger is DangerClass.NETWORK


# ── run(): happy + every failure mode is graceful ─────────────────────────────


class _FakeFetcher:
    """Stands in for SafeFetcher: returns a canned outcome or raises a canned error."""

    def __init__(self, *, outcome: FetchOutcome | None = None, error: Exception | None = None):
        self._outcome = outcome
        self._error = error

    async def fetch(self, url: str) -> FetchOutcome:
        if self._error is not None:
            raise self._error
        assert self._outcome is not None
        return self._outcome


def _tool(**kwargs: object) -> WebFetchTool:
    return WebFetchTool(fetcher=kwargs.pop("fetcher"), **kwargs)  # type: ignore[arg-type]


async def test_run_extracts_text_on_success() -> None:
    outcome = FetchOutcome(
        final_url="https://example.com/a",
        status_code=200,
        content_type="text/html; charset=utf-8",
        body=b"<html><title>T</title><body><p>Hello world.</p></body></html>",
        truncated=False,
    )
    result = await _tool(fetcher=_FakeFetcher(outcome=outcome)).run(
        WebFetchArgs(url="https://example.com/a")
    )

    assert result.success is True
    assert result.output["title"] == "T"
    assert "Hello world." in str(result.output["text"])
    assert result.output["url"] == "https://example.com/a"


async def test_run_caps_extracted_text() -> None:
    body = b"<body>" + b"a" * 5000 + b"</body>"
    outcome = FetchOutcome(
        final_url="https://example.com/big",
        status_code=200,
        content_type="text/html",
        body=body,
        truncated=False,
    )
    result = await _tool(fetcher=_FakeFetcher(outcome=outcome), max_text_chars=100).run(
        WebFetchArgs(url="https://example.com/big")
    )

    assert result.success is True
    assert len(str(result.output["text"])) == 100
    assert result.output["truncated"] is True


async def test_run_renders_ssrf_block_as_graceful_failure() -> None:
    result = await _tool(
        fetcher=_FakeFetcher(error=SsrfError("host resolves to a blocked address"))
    ).run(WebFetchArgs(url="https://evil.example/"))
    assert result.success is False
    assert result.output["error"] == "blocked"


async def test_run_renders_timeout_as_graceful_failure() -> None:
    result = await _tool(fetcher=_FakeFetcher(error=httpx.ConnectTimeout("slow"))).run(
        WebFetchArgs(url="https://example.com/")
    )
    assert result.success is False
    assert result.output["error"] == "timeout"


async def test_run_renders_http_error_as_graceful_failure() -> None:
    result = await _tool(fetcher=_FakeFetcher(error=httpx.ConnectError("refused"))).run(
        WebFetchArgs(url="https://example.com/")
    )
    assert result.success is False
    assert result.output["error"] == "http_error"


async def test_run_reports_4xx_as_failure() -> None:
    outcome = FetchOutcome(
        final_url="https://example.com/missing",
        status_code=404,
        content_type="text/html",
        body=b"nope",
        truncated=False,
    )
    result = await _tool(fetcher=_FakeFetcher(outcome=outcome)).run(
        WebFetchArgs(url="https://example.com/missing")
    )
    assert result.success is False
    assert result.output["error"] == "http_status"
    assert result.output["status"] == 404


async def test_run_rejects_non_text_content() -> None:
    outcome = FetchOutcome(
        final_url="https://example.com/img.png",
        status_code=200,
        content_type="image/png",
        body=b"\x89PNG\r\n",
        truncated=False,
    )
    result = await _tool(fetcher=_FakeFetcher(outcome=outcome)).run(
        WebFetchArgs(url="https://example.com/img.png")
    )
    assert result.success is False
    assert result.output["error"] == "non_text"


async def test_run_returns_plain_text_collapsed_without_html_parsing() -> None:
    # text/plain takes the non-HTML branch: no title, body collapsed (runs of blank
    # lines → one, trailing space trimmed) — distinct from the readability extractor.
    outcome = FetchOutcome(
        final_url="https://example.com/raw.txt",
        status_code=200,
        content_type="text/plain; charset=utf-8",
        body=b"line one\n\n\n   line two   ",
        truncated=False,
    )
    result = await _tool(fetcher=_FakeFetcher(outcome=outcome)).run(
        WebFetchArgs(url="https://example.com/raw.txt")
    )
    assert result.success is True
    assert result.output["title"] == ""
    assert result.output["text"] == "line one\n\nline two"
