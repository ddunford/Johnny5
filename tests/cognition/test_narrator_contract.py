"""TC-2.13 — the narrator output-shape contract (captured-fixture discipline).

The house rule: every adapter that parses a model response is pinned by a literal
captured envelope so a model output-shape change surfaces *here*, not silently in
production cognition. This locks the two projection layers the inner monologue
depends on, against REAL gemma4 output:

1. ``parse_chat_completion`` (provider layer) takes ``message.content`` first, so a
   thinking model's ``message.reasoning`` never becomes the answer.
2. ``parse_thought`` (narrator layer) projects the JSON ``{"thought": ...}`` content
   into the thought text — and the reasoning chain must never leak into it.

Pure (no I/O, no network) — feeds captured envelopes straight through the parsers.

KEY FINDING captured here: under the reflective narrator persona + ``json_object``,
gemma4:e4b IS a thinking model (it emits a separate ``reasoning`` channel), contrary
to the substrate doc. ``ollama_gemma4_narrator_thought.json`` is the real envelope
with both channels; the no-leak assertion below is exactly why that matters.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from brain.agents.narrator import NarratorResponse, parse_thought
from brain.llm.providers.openai_compatible import parse_chat_completion

pytestmark = pytest.mark.contract

FixtureLoader = Callable[[str], Any]

# Backend's content-only capture (content string + expected_thought).
CONTENT_FIXTURE = "llm/narrator_gemma4_thought.json"
# QA's full-envelope capture (content + the reasoning channel + finish_reason).
ENVELOPE_FIXTURE = "llm/ollama_gemma4_narrator_thought.json"
# A real thinking-model envelope reused to prove reasoning stays out of content.
QWEN_FIXTURE = "llm/ollama_qwen35_thinking_completion.json"


def test_parse_thought_projects_real_gemma4_content(load_fixture: FixtureLoader) -> None:
    """parse_thought turns gemma4's clean JSON content into the thought text."""
    fixture = load_fixture(CONTENT_FIXTURE)
    thought = parse_thought(fixture["content"])

    assert thought == fixture["expected_thought"]
    # The projection is the JSON's "thought" value — no envelope/JSON artifacts.
    assert not thought.startswith("{")
    assert thought == thought.strip()


def test_full_envelope_projects_to_thought_without_reasoning_leakage(
    load_fixture: FixtureLoader,
) -> None:
    """The real two-layer path on a captured gemma4 envelope that carries BOTH a
    reasoning chain and the JSON content: content-first projection, then the thought
    — and the 994-char reasoning never appears in the thought."""
    envelope = load_fixture(ENVELOPE_FIXTURE)
    message = envelope["choices"][0]["message"]
    reasoning = message["reasoning"]
    assert reasoning, "fixture must carry a reasoning channel for this to mean anything"

    # Layer 1: the provider projects content-first (reasoning kept separate).
    completion = parse_chat_completion(envelope, provider="ollama", requested_model="gemma4:e4b")
    assert completion.content == message["content"]  # the JSON, NOT the reasoning
    assert completion.reasoning == reasoning
    assert completion.content != completion.reasoning

    # Layer 2: the narrator projects the JSON content into the thought.
    thought = parse_thought(completion.content)
    assert thought
    # The reasoning chain-of-thought must NOT have leaked into the thought.
    assert reasoning not in thought
    assert "Thinking Process" not in thought
    assert "**" not in thought  # the reasoning's markdown scaffolding never surfaces
    # And the thought is exactly the model's JSON "thought" value.
    assert thought == json.loads(message["content"])["thought"].strip()


def test_thinking_model_reasoning_never_becomes_the_content(
    load_fixture: FixtureLoader,
) -> None:
    """On a real qwen thinking envelope, the long chain-of-thought stays in the
    reasoning channel — the content (what the narrator would parse) is the clean
    answer. This is the precondition that keeps reasoning out of any thought."""
    envelope = load_fixture(QWEN_FIXTURE)
    message = envelope["choices"][0]["message"]

    completion = parse_chat_completion(
        envelope, provider="ollama", requested_model="qwen3.5-9b-128k"
    )

    assert completion.content == message["content"]  # clean answer
    assert completion.reasoning == message["reasoning"]  # the chain, kept apart
    assert len(completion.reasoning or "") > len(completion.content)  # reasoning is the bulk
    assert "Thinking Process" not in completion.content


def test_parse_thought_rejects_a_non_json_response() -> None:
    """A model that ignores the JSON contract (returns prose) must fail loudly at
    the parse seam — the router treats it as a schema failure and fails over,
    rather than a malformed thought slipping into the monologue."""
    with pytest.raises(ValueError):  # pydantic ValidationError is a ValueError
        parse_thought("Pixel sounds like a lovely dog!")


def test_parse_thought_matches_the_narrator_response_schema(
    load_fixture: FixtureLoader,
) -> None:
    """The content validates against the very schema the narrator asks the model
    for (NarratorResponse) — the request contract and the parse contract agree."""
    content = load_fixture(CONTENT_FIXTURE)["content"]
    model = NarratorResponse.model_validate_json(content)
    assert model.thought == parse_thought(content)
