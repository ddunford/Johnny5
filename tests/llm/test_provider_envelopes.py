"""Contract guards on the *captured* provider response envelopes.

The fixtures under ``tests/fixtures/llm/`` are LITERAL captures of real
responses (provenance in ``manifest.json``):

  * Groq ``llama-3.3-70b-versatile``   — cloud, OpenAI-compatible
  * Ollama ``gemma4:e4b``               — local workhorse, clean ``content``
  * Ollama ``qwen3.5-9b-128k:latest``   — local "thinking" model
  * ``bge-m3`` embeddings (:8002/embed) — custom Flask contract

These tests lock the envelope SHAPE the provider adapters (``brain/llm/
providers/*``) must project. They are intentionally adapter-free: they assert
what the wire actually returns today, so a silent provider-shape drift (or an
accidental hand-edit of a fixture) fails loudly. When the adapters land
(TASK-0.7), ``test_provider_adapters.py`` feeds these same fixtures through the
adapters and asserts the projection.

KEY FINDING locked here: contrary to the substrate doc's "empty content"
claim, ``qwen3.5-9b-128k`` returns a CLEAN, POPULATED ``content`` plus a
SEPARATE ``reasoning`` field carrying the chain-of-thought. The adapter must
read ``content`` as the answer and treat ``reasoning`` as a side channel.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

# Type of the ``load_fixture`` session fixture (see conftest.py).
FixtureLoader = Callable[[str], Any]

pytestmark = pytest.mark.contract

GROQ = "llm/groq_llama33_completion.json"
GEMMA4 = "llm/ollama_gemma4_completion.json"
QWEN = "llm/ollama_qwen35_thinking_completion.json"
QWEN_TERSE = "llm/ollama_qwen35_thinking_completion_terse.json"
EMBED = "llm/embed_bge_m3.json"


def _message(envelope: dict) -> dict:
    choices = envelope["choices"]
    assert isinstance(choices, list) and len(choices) >= 1
    return choices[0]["message"]


def _usage_is_openai_shaped(usage: dict) -> bool:
    return {"prompt_tokens", "completion_tokens", "total_tokens"} <= set(usage)


# --------------------------------------------------------------------------- #
# Groq (cloud)                                                                 #
# --------------------------------------------------------------------------- #


def test_groq_envelope_clean_content(load_fixture: FixtureLoader) -> None:
    env = load_fixture(GROQ)
    msg = _message(env)
    assert msg["role"] == "assistant" or msg["role"]  # role present
    assert msg["content"].strip() == "ALIVE"
    assert "reasoning" not in msg  # Groq llama-3.3 has no separate reasoning channel


def test_groq_usage_has_token_counts(load_fixture: FixtureLoader) -> None:
    env = load_fixture(GROQ)
    usage = env["usage"]
    assert _usage_is_openai_shaped(usage)
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


# --------------------------------------------------------------------------- #
# Ollama gemma4:e4b (local workhorse)                                          #
# --------------------------------------------------------------------------- #


def test_gemma4_envelope_clean_content(load_fixture: FixtureLoader) -> None:
    env = load_fixture(GEMMA4)
    msg = _message(env)
    assert msg["content"].strip() == "ALIVE"
    assert "reasoning" not in msg  # gemma4 is not a thinking model


def test_gemma4_usage_has_token_counts(load_fixture: FixtureLoader) -> None:
    env = load_fixture(GEMMA4)
    assert _usage_is_openai_shaped(env["usage"])


# --------------------------------------------------------------------------- #
# Ollama qwen3.5-9b-128k (thinking model) — the critical envelope             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fixture", [QWEN, QWEN_TERSE])
def test_qwen_has_both_clean_content_and_separate_reasoning(
    load_fixture: FixtureLoader, fixture: str
) -> None:
    """The defining quirk: content is populated AND there's a `reasoning` field.

    Refutes the substrate doc's "empty content" claim. Both must be present;
    the answer lives in `content`, the chain-of-thought in `reasoning`.
    """
    msg = _message(load_fixture(fixture))
    content = msg.get("content")
    reasoning = msg.get("reasoning")

    assert content is not None and content.strip(), "qwen content was empty"
    assert reasoning is not None and reasoning.strip(), "qwen reasoning channel missing"


def test_qwen_content_is_the_answer_not_the_reasoning(load_fixture: FixtureLoader) -> None:
    """content holds the concise answer; the verbose trace stays in reasoning."""
    msg = _message(load_fixture(QWEN))
    content = msg["content"]
    reasoning = msg["reasoning"]

    # The arithmetic answer is in content.
    assert "4" in content
    # The deliberation trace ("Thinking Process") is in reasoning, NOT content.
    assert "Thinking Process" in reasoning
    assert "Thinking Process" not in content
    # The trace is far longer than the answer — they are genuinely separated.
    assert len(reasoning) > len(content) * 5


def test_qwen_terse_content_is_single_word(load_fixture: FixtureLoader) -> None:
    msg = _message(load_fixture(QWEN_TERSE))
    assert msg["content"].strip() == "Red"


def test_qwen_usage_has_token_counts(load_fixture: FixtureLoader) -> None:
    assert _usage_is_openai_shaped(load_fixture(QWEN)["usage"])


# --------------------------------------------------------------------------- #
# Embeddings (:8002/embed — custom Flask contract, NOT TEI native)            #
# --------------------------------------------------------------------------- #


def test_embed_envelope_shape(load_fixture: FixtureLoader) -> None:
    env = load_fixture(EMBED)
    assert "embeddings" in env
    assert isinstance(env["embeddings"], list)


def test_embed_returns_single_1024d_float_vector(load_fixture: FixtureLoader) -> None:
    env = load_fixture(EMBED)
    embeddings = env["embeddings"]
    assert len(embeddings) == 1  # one input string -> one vector
    vector = embeddings[0]
    assert len(vector) == 1024, "bge-m3 must return 1024-d vectors"
    assert all(isinstance(x, (int, float)) for x in vector)
    assert any(x != 0 for x in vector)  # not a zero/degenerate vector
