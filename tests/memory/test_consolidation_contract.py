"""Consolidation contract: model response → ConsolidationSummary projection (TC-4.9).

The house rule (FC-4): the consolidation parse seam ``parse_consolidation`` (model
JSON → ``ConsolidationSummary``) is pinned by a literal envelope so a model output-
shape change surfaces *here*, not silently as a corrupted/empty semantic fact. Pure
(no I/O, no DB, no LLM); the clustering + persistence behaviour is covered separately
in ``test_consolidator.py``, and the real Groq/qwen token-budget path is the
``@pytest.mark.live`` leg in ``test_consolidation_live.py``.

The reasoning-leakage leg reuses the captured qwen thinking envelope's reasoning
channel paired with a representative summary content, proving the two-layer path
(``parse_chat_completion`` content-first → ``parse_consolidation``) keeps a thinking
model's chain-of-thought out of the stored fact.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from brain.llm.providers.openai_compatible import parse_chat_completion
from brain.memory.consolidator import ConsolidationSummary, parse_consolidation

pytestmark = pytest.mark.contract

FixtureLoader = Callable[[str], Any]

QWEN_FIXTURE = "llm/ollama_qwen35_thinking_completion.json"

_VALID_SUMMARY = {
    "subject": "the lab rig",
    "predicate": "tends to",
    "object": "overheat under sustained load, which I noticed before the alarm",
    "confidence": 0.7,
}
_VALID_SUMMARY_JSON = json.dumps(_VALID_SUMMARY)


def test_parse_consolidation_projects_a_valid_response() -> None:
    """A well-formed consolidation JSON projects field-for-field into a summary."""
    summary = parse_consolidation(_VALID_SUMMARY_JSON)

    assert summary.subject == _VALID_SUMMARY["subject"]
    assert summary.predicate == _VALID_SUMMARY["predicate"]
    assert summary.object == _VALID_SUMMARY["object"]
    assert summary.confidence == pytest.approx(0.7)


def test_confidence_defaults_when_omitted() -> None:
    """A summary without an explicit confidence takes the model default — a valid
    triple, not a parse failure."""
    summary = parse_consolidation(
        json.dumps({"subject": "the rig", "predicate": "runs", "object": "hot"})
    )
    assert summary.confidence == pytest.approx(0.6)


def test_empty_content_fails_loudly() -> None:
    """A truncated empty body must raise so the consolidator falls back to the
    deterministic fact rather than writing an empty one."""
    with pytest.raises(ValueError):  # pydantic ValidationError is a ValueError
        parse_consolidation("")


def test_non_json_prose_fails_loudly() -> None:
    with pytest.raises(ValueError):
        parse_consolidation("The lab rig has been running hot lately.")


def test_missing_required_field_fails_loudly() -> None:
    """``subject``/``predicate``/``object`` are required — a partial triple is a
    schema failure, not a half-formed fact."""
    with pytest.raises(ValueError):
        parse_consolidation(json.dumps({"subject": "the rig", "predicate": "runs"}))


def test_two_layer_projection_keeps_reasoning_out_of_the_fact(
    load_fixture: FixtureLoader,
) -> None:
    """The real two-layer path on an envelope carrying BOTH a (captured) reasoning
    chain and the JSON content: content-first at the provider, then the summary — and
    the chain-of-thought never leaks into the stored fact."""
    reasoning = load_fixture(QWEN_FIXTURE)["choices"][0]["message"]["reasoning"]
    assert reasoning, "fixture must carry a reasoning channel for this to mean anything"
    envelope = {
        "model": "qwen3.5-9b-128k",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": _VALID_SUMMARY_JSON,
                    "reasoning": reasoning,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200},
    }

    completion = parse_chat_completion(
        envelope, provider="ollama", requested_model="qwen3.5-9b-128k"
    )
    assert completion.content == _VALID_SUMMARY_JSON  # the JSON, not the reasoning
    assert completion.reasoning == reasoning

    summary = parse_consolidation(completion.content)
    assert reasoning not in summary.object
    assert reasoning not in summary.subject
    assert "Thinking" not in summary.object


def test_parse_matches_the_request_schema() -> None:
    """The content validates against the very schema the consolidator asks for."""
    model = ConsolidationSummary.model_validate_json(_VALID_SUMMARY_JSON)
    assert model == parse_consolidation(_VALID_SUMMARY_JSON)
