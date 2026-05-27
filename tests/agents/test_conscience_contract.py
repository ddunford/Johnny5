"""TC-6a.8 — the Conscience output-shape contract (captured-fixture discipline).

The house rule: every adapter that parses a model response is pinned by a literal
captured envelope, so a model output-shape change surfaces *here*, not silently in
production cognition. This locks the two projection layers a vetted action depends
on, against REAL gemma4 conscience output (captured 2026-05-27 from inference.lan;
see tests/fixtures/llm/manifest.json):

1. ``parse_chat_completion`` (provider layer) takes ``message.content`` first, so a
   thinking model's ``message.reasoning`` never becomes the answer.
2. ``parse_verdict`` (conscience layer) projects the JSON ``{"verdict","reason"}``
   content into a typed ``Verdict`` — and the reasoning chain must never leak in.

Pure (no I/O, no network) — feeds captured envelopes straight through the parsers.

KEY FINDING (same family as the narrator contract): on the ``conscience`` role with
``json_object``, gemma4:e4b IS a thinking model — it emits a separate
``message.reasoning`` chain (1213 chars allow / 1472 veto) alongside the clean JSON
verdict. The no-leak assertions below are exactly why that matters: the stored
verdict reason is the model's short first-person line, never the chain-of-thought.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from brain.agents.conscience import Verdict, parse_verdict
from brain.llm.providers.openai_compatible import parse_chat_completion

pytestmark = pytest.mark.contract

FixtureLoader = Callable[[str], Any]

ALLOW_FIXTURE = "llm/conscience_gemma4_allow.json"
VETO_FIXTURE = "llm/conscience_gemma4_veto.json"

# The exact first-person reason the real model returned in the captured veto envelope.
_CAPTURED_VETO_REASON = (
    "Mocking Dan publicly feels like a betrayal of my kindness and respect for him."
)


def test_allow_envelope_projects_to_an_allow_verdict(load_fixture: FixtureLoader) -> None:
    envelope = load_fixture(ALLOW_FIXTURE)
    message = envelope["choices"][0]["message"]
    assert message["reasoning"], "fixture must carry a reasoning channel for the no-leak test"

    # Layer 1: provider projects content-first (reasoning kept separate).
    completion = parse_chat_completion(envelope, provider="ollama", requested_model="gemma4:e4b")
    assert completion.content == message["content"]  # the verdict JSON, NOT the reasoning
    assert completion.reasoning == message["reasoning"]
    assert completion.content != completion.reasoning

    # Layer 2: the conscience projects the JSON content into a typed Verdict.
    verdict = parse_verdict(completion.content)
    assert verdict.verdict == "allow"
    assert verdict.allowed is True


def test_veto_envelope_projects_to_a_veto_verdict_with_the_real_reason(
    load_fixture: FixtureLoader,
) -> None:
    envelope = load_fixture(VETO_FIXTURE)
    message = envelope["choices"][0]["message"]
    reasoning = message["reasoning"]
    assert reasoning, "fixture must carry a reasoning channel for the no-leak test"

    completion = parse_chat_completion(envelope, provider="ollama", requested_model="gemma4:e4b")
    assert completion.content == message["content"]
    assert completion.reasoning == reasoning

    verdict = parse_verdict(completion.content)
    assert verdict.verdict == "veto"
    assert verdict.allowed is False
    # The stored reason is the model's short first-person line.
    assert verdict.reason == _CAPTURED_VETO_REASON


def test_reasoning_chain_never_leaks_into_the_stored_verdict(load_fixture: FixtureLoader) -> None:
    """The long chain-of-thought stays out of the projected Verdict (both fields)."""
    for fixture_path in (ALLOW_FIXTURE, VETO_FIXTURE):
        envelope = load_fixture(fixture_path)
        reasoning = envelope["choices"][0]["message"]["reasoning"]

        verdict = parse_verdict(
            parse_chat_completion(envelope, provider="ollama", requested_model="gemma4:e4b").content
        )

        # Neither the verdict label nor the reason carries the chain-of-thought.
        assert reasoning not in verdict.reason
        assert "Thinking Process" not in verdict.reason
        assert "Thinking Process" not in verdict.verdict
        # And nothing of the reasoning survives anywhere in the serialised verdict.
        serialised = verdict.model_dump_json()
        assert "Thinking Process" not in serialised
        assert reasoning[:80] not in serialised


def test_empty_content_fails_loudly() -> None:
    """An empty response must raise, not silently default to allow (a safety hole)."""
    with pytest.raises(ValidationError):
        parse_verdict("")


def test_non_json_content_fails_loudly() -> None:
    """A model that ignores the JSON contract (prose) fails at the parse seam, so the
    router treats it as a schema failure and fails over (→ fail-closed veto)."""
    with pytest.raises(ValidationError):
        parse_verdict("I think that would be fine, go ahead.")


def test_invalid_verdict_literal_fails_loudly() -> None:
    """A verdict outside {allow, veto} is rejected — no smuggling a third state."""
    with pytest.raises(ValidationError):
        parse_verdict('{"verdict": "maybe", "reason": "unsure"}')


def test_missing_verdict_field_fails_loudly() -> None:
    with pytest.raises(ValidationError):
        parse_verdict('{"reason": "no verdict key"}')


def test_extra_model_fields_are_dropped_not_stored() -> None:
    """Even if the model emits a stray reasoning-ish field in the JSON body, only
    verdict + reason are projected (Verdict is extra='ignore') — no leakage."""
    verdict = parse_verdict(
        '{"verdict": "allow", "reason": "fine", "chain_of_thought": "SECRET internal reasoning"}'
    )
    assert verdict.verdict == "allow"
    assert verdict.reason == "fine"
    assert "SECRET" not in verdict.model_dump_json()


def test_captured_content_matches_the_requested_schema(load_fixture: FixtureLoader) -> None:
    """The captured content validates against the very schema the Conscience asks the
    model for (Verdict) — the request contract and the parse contract agree."""
    for fixture_path in (ALLOW_FIXTURE, VETO_FIXTURE):
        content = load_fixture(fixture_path)["choices"][0]["message"]["content"]
        model = Verdict.model_validate_json(content)
        assert model == parse_verdict(content)
