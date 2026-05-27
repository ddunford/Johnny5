"""Contract tests for the Groq + Ollama provider projection.

House rule: every adapter that parses a model response has a contract test that
feeds it a LITERAL captured envelope and asserts the projection, so a model
output-shape change can't silently break cognition.

The projection under test is the pure ``parse_chat_completion`` function (shared
by GroqProvider and OllamaProvider via OpenAICompatibleProvider). Being I/O-free,
it takes captured fixtures directly. Fixtures under ``tests/fixtures/llm/`` are
real responses captured live from inference.lan / Groq (provenance in
``manifest.json``). The two derived cases — empty ``content`` and the
``reasoning_content`` alias — start from a real envelope and mutate one field to
exercise the documented thinking-model safeguards.

Transport-level error mapping (HTTP ≥400 → ProviderError, timeout →
ProviderTimeoutError) is covered with httpx's built-in MockTransport, since the
router's circuit breaker keys off exactly those exceptions.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any, NoReturn

import httpx
import pytest

from brain.llm.base import (
    Completion,
    Message,
    ProviderError,
    ProviderTimeoutError,
)
from brain.llm.providers.groq import GROQ_PROVIDER_NAME
from brain.llm.providers.ollama import OLLAMA_PROVIDER_NAME
from brain.llm.providers.openai_compatible import (
    OpenAICompatibleProvider,
    parse_chat_completion,
)

pytestmark = pytest.mark.contract

FixtureLoader = Callable[[str], Any]

GROQ = "llm/groq_llama33_completion.json"
GEMMA4 = "llm/ollama_gemma4_completion.json"
QWEN = "llm/ollama_qwen35_thinking_completion.json"
QWEN_TERSE = "llm/ollama_qwen35_thinking_completion_terse.json"


# --------------------------------------------------------------------------- #
# Groq projection (cloud)                                                      #
# --------------------------------------------------------------------------- #


def test_groq_projection_clean_content(load_fixture: FixtureLoader) -> None:
    env = load_fixture(GROQ)
    c = parse_chat_completion(
        env, provider=GROQ_PROVIDER_NAME, requested_model="llama-3.3-70b-versatile"
    )
    assert isinstance(c, Completion)
    assert c.content == "ALIVE"
    assert c.reasoning is None
    assert c.provider == GROQ_PROVIDER_NAME
    assert c.model == "llama-3.3-70b-versatile"
    assert c.prompt_tokens == 41
    assert c.completion_tokens == 3
    assert c.finish_reason == "stop"


def test_groq_ignores_extra_keys_and_preserves_raw(load_fixture: FixtureLoader) -> None:
    """Groq adds usage_breakdown / x_groq / service_tier — the parser must not
    choke on them, and the full envelope is preserved on ``raw``."""
    env = load_fixture(GROQ)
    assert {"x_groq", "service_tier", "usage_breakdown"} <= set(env)  # guard the fixture
    c = parse_chat_completion(
        env, provider=GROQ_PROVIDER_NAME, requested_model="llama-3.3-70b-versatile"
    )
    assert c.raw == env
    assert "x_groq" in c.raw


# --------------------------------------------------------------------------- #
# Ollama gemma4:e4b projection (local workhorse, clean content)               #
# --------------------------------------------------------------------------- #


def test_ollama_gemma4_projection_clean_content(load_fixture: FixtureLoader) -> None:
    env = load_fixture(GEMMA4)
    c = parse_chat_completion(env, provider=OLLAMA_PROVIDER_NAME, requested_model="gemma4:e4b")
    assert c.content == "ALIVE"
    assert c.reasoning is None
    assert c.provider == OLLAMA_PROVIDER_NAME
    assert c.model == "gemma4:e4b"
    assert c.prompt_tokens == 22
    assert c.completion_tokens == 3
    assert c.finish_reason == "stop"


# --------------------------------------------------------------------------- #
# Ollama qwen3.5-9b-128k projection (THINKING model — the critical adapter)   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fixture,expected_content,prompt_toks,completion_toks",
    [
        (QWEN, "2 plus 2 equals 4.", 24, 1510),
        (QWEN_TERSE, "Red", 21, 269),
    ],
)
def test_ollama_qwen_projects_answer_not_reasoning(
    load_fixture: FixtureLoader,
    fixture: str,
    expected_content: str,
    prompt_toks: int,
    completion_toks: int,
) -> None:
    """content is the clean ANSWER; the chain-of-thought stays in reasoning."""
    env = load_fixture(fixture)
    c = parse_chat_completion(
        env, provider=OLLAMA_PROVIDER_NAME, requested_model="qwen3.5-9b-128k:latest"
    )
    assert c.content == expected_content
    assert c.reasoning is not None and c.reasoning.strip()
    # The answer must not be the scratch-work.
    assert "Thinking Process" not in c.content
    assert c.provider == OLLAMA_PROVIDER_NAME
    assert c.model == "qwen3.5-9b-128k:latest"
    assert c.prompt_tokens == prompt_toks
    assert c.completion_tokens == completion_toks


def test_ollama_qwen_reasoning_carries_the_trace(load_fixture: FixtureLoader) -> None:
    c = parse_chat_completion(
        load_fixture(QWEN), provider=OLLAMA_PROVIDER_NAME, requested_model="qwen3.5-9b-128k:latest"
    )
    assert c.reasoning is not None
    assert "Thinking Process" in c.reasoning
    assert len(c.reasoning) > len(c.content) * 5


def test_ollama_empty_content_falls_back_to_reasoning(load_fixture: FixtureLoader) -> None:
    """Documented thinking-model safeguard: a real qwen envelope whose answer
    channel came back blank must surface the reasoning, never an empty string."""
    env = deepcopy(load_fixture(QWEN_TERSE))
    reasoning_text = env["choices"][0]["message"]["reasoning"]
    env["choices"][0]["message"]["content"] = ""  # provider returned a blank answer
    c = parse_chat_completion(
        env, provider=OLLAMA_PROVIDER_NAME, requested_model="qwen3.5-9b-128k:latest"
    )
    assert c.content == reasoning_text
    assert c.content.strip()
    assert c.reasoning == reasoning_text


def test_ollama_accepts_reasoning_content_alias(load_fixture: FixtureLoader) -> None:
    """Some OpenAI-compatible servers name the channel ``reasoning_content``;
    the parser accepts either spelling."""
    env = deepcopy(load_fixture(QWEN_TERSE))
    msg = env["choices"][0]["message"]
    msg["reasoning_content"] = msg.pop("reasoning")
    msg["content"] = ""
    c = parse_chat_completion(
        env, provider=OLLAMA_PROVIDER_NAME, requested_model="qwen3.5-9b-128k:latest"
    )
    assert c.reasoning == msg["reasoning_content"]
    assert c.content == c.reasoning  # fell back via the alias


# --------------------------------------------------------------------------- #
# Projection edge cases                                                        #
# --------------------------------------------------------------------------- #


def test_model_defaults_to_requested_when_absent(load_fixture: FixtureLoader) -> None:
    env = deepcopy(load_fixture(GEMMA4))
    del env["model"]
    c = parse_chat_completion(env, provider=OLLAMA_PROVIDER_NAME, requested_model="gemma4:e4b")
    assert c.model == "gemma4:e4b"


def test_empty_choices_raises_provider_error() -> None:
    with pytest.raises(ProviderError) as exc_info:
        parse_chat_completion({"choices": []}, provider=OLLAMA_PROVIDER_NAME, requested_model="m")
    assert exc_info.value.provider == OLLAMA_PROVIDER_NAME


def test_missing_choices_key_raises_provider_error() -> None:
    with pytest.raises(ProviderError) as exc_info:
        parse_chat_completion({}, provider=GROQ_PROVIDER_NAME, requested_model="m")
    assert exc_info.value.provider == GROQ_PROVIDER_NAME


# --------------------------------------------------------------------------- #
# Transport error mapping (httpx MockTransport — no network)                  #
# --------------------------------------------------------------------------- #


async def _mock_transport_provider(
    name: str, handler: Callable[[httpx.Request], httpx.Response]
) -> OpenAICompatibleProvider:
    """Build a provider whose HTTP client is backed by a mock transport."""
    provider = OpenAICompatibleProvider(name=name, base_url="http://test")
    await provider.aclose()  # discard the real client created in __init__
    provider._client = httpx.AsyncClient(
        base_url="http://test/", transport=httpx.MockTransport(handler)
    )
    return provider


async def test_complete_parses_success_through_transport() -> None:
    body = {
        "model": "gemma4:e4b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ALIVE"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    provider = await _mock_transport_provider(
        OLLAMA_PROVIDER_NAME, lambda req: httpx.Response(200, json=body)
    )
    try:
        c = await provider.complete([Message(role="user", content="hi")], model="gemma4:e4b")
        assert c.content == "ALIVE"
        assert c.provider == OLLAMA_PROVIDER_NAME
        assert c.finish_reason == "stop"
    finally:
        await provider.aclose()


async def test_http_error_maps_to_provider_error_with_status() -> None:
    provider = await _mock_transport_provider(
        GROQ_PROVIDER_NAME, lambda req: httpx.Response(503, text="upstream down")
    )
    try:
        with pytest.raises(ProviderError) as exc_info:
            await provider.complete([Message(role="user", content="hi")], model="m")
        assert exc_info.value.status_code == 503
        assert exc_info.value.provider == GROQ_PROVIDER_NAME
    finally:
        await provider.aclose()


async def test_timeout_maps_to_provider_timeout_error() -> None:
    def _timeout(request: httpx.Request) -> NoReturn:
        raise httpx.ReadTimeout("slow", request=request)

    provider = await _mock_transport_provider(OLLAMA_PROVIDER_NAME, _timeout)
    try:
        with pytest.raises(ProviderTimeoutError) as exc_info:
            await provider.complete([Message(role="user", content="hi")], model="m")
        assert exc_info.value.provider == OLLAMA_PROVIDER_NAME
    finally:
        await provider.aclose()


async def test_empty_choices_over_transport_raises_provider_error() -> None:
    provider = await _mock_transport_provider(
        OLLAMA_PROVIDER_NAME, lambda req: httpx.Response(200, json={"choices": []})
    )
    try:
        with pytest.raises(ProviderError):
            await provider.complete([Message(role="user", content="hi")], model="m")
    finally:
        await provider.aclose()
