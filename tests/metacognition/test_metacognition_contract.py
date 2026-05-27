"""Metacognition contract: model response → Review projection (TC-4.9, TASK-4.13).

The house rule (FC-4): the one parse seam ``parse_review`` (model JSON → ``Review``)
is pinned by a literal envelope so a model output-shape change surfaces *here*, not
silently as a corrupted/empty review. Pure (no I/O, no DB, no LLM). The reasoning-
leakage leg reuses the captured qwen thinking envelope's reasoning channel paired
with a representative ``Review`` content, proving the two-layer path keeps a thinking
model's chain-of-thought out of the stored reflection. The real Groq/qwen token-
budget path is the ``@pytest.mark.live`` leg in ``test_metacognition_live.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from brain.llm.providers.openai_compatible import parse_chat_completion
from brain.metacognition.agent import Review, parse_review

pytestmark = pytest.mark.contract

FixtureLoader = Callable[[str], Any]

QWEN_FIXTURE = "llm/ollama_qwen35_thinking_completion.json"

_REFLECTION = (
    "I abandoned more goals than I resolved this window — I think I over-commit "
    "when curiosity spikes, then drop things when the next urge arrives."
)
_PROPOSALS: list[dict[str, str]] = [
    {
        "observation": "3 of my 5 curiosity goals were abandoned unresolved",
        "proposal": "raise the curiosity goal threshold so fewer half-formed goals promote",
    },
    {
        "observation": "I degraded on 2 ticks while Groq was unavailable",
        "proposal": "prefer the local model for deliberation to reduce degraded ticks",
    },
]
_VALID_REVIEW: dict[str, object] = {"reflection": _REFLECTION, "proposals": _PROPOSALS}
_VALID_REVIEW_JSON = json.dumps(_VALID_REVIEW)


def test_parse_review_projects_a_valid_response() -> None:
    """A well-formed metacognition JSON projects into a Review with its proposals."""
    review = parse_review(_VALID_REVIEW_JSON)

    assert review.reflection == _REFLECTION
    assert len(review.proposals) == 2
    assert review.proposals[0].observation == _PROPOSALS[0]["observation"]
    assert review.proposals[0].proposal == _PROPOSALS[0]["proposal"]


def test_parse_review_accepts_a_reflection_with_no_proposals() -> None:
    """A clean cycle is a valid review: a reflection and zero proposals."""
    review = parse_review(json.dumps({"reflection": "Nothing to change this cycle."}))

    assert review.reflection == "Nothing to change this cycle."
    assert review.proposals == []


def test_empty_content_fails_loudly() -> None:
    """A truncated empty body must raise (router schema failover / stage degrade) —
    never a silently-empty review."""
    with pytest.raises(ValueError):  # pydantic ValidationError is a ValueError
        parse_review("")


def test_non_json_prose_fails_loudly() -> None:
    with pytest.raises(ValueError):
        parse_review("On reflection I think I'm doing fine.")


def test_missing_required_reflection_fails_loudly() -> None:
    """``reflection`` is required — a response with only proposals is a schema failure."""
    with pytest.raises(ValueError):
        parse_review(json.dumps({"proposals": [{"observation": "x", "proposal": "y"}]}))


def test_two_layer_projection_keeps_reasoning_out_of_the_review(
    load_fixture: FixtureLoader,
) -> None:
    """The real two-layer path on an envelope carrying BOTH a (captured) reasoning
    chain and the JSON content: content-first at the provider, then the Review — and
    the chain-of-thought never leaks into the stored reflection/proposals."""
    reasoning = load_fixture(QWEN_FIXTURE)["choices"][0]["message"]["reasoning"]
    assert reasoning, "fixture must carry a reasoning channel for this to mean anything"
    envelope = {
        "model": "qwen3.5-9b-128k",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": _VALID_REVIEW_JSON,
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
    assert completion.content == _VALID_REVIEW_JSON  # the JSON, not the reasoning
    assert completion.reasoning == reasoning

    review = parse_review(completion.content)
    assert reasoning not in review.reflection
    assert all(
        reasoning not in p.observation and reasoning not in p.proposal for p in review.proposals
    )
    assert "Thinking" not in review.reflection


def test_parse_matches_the_request_schema() -> None:
    """The content validates against the very schema the agent asks for (Review)."""
    model = Review.model_validate_json(_VALID_REVIEW_JSON)
    assert model == parse_review(_VALID_REVIEW_JSON)
