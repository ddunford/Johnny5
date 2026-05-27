"""Self-model contract: model response → IdentityDelta projection (TC-4.9, TASK-4.13).

The house rule (FC-4): every adapter that parses a model response is pinned by a
literal envelope so a model output-shape change surfaces *here*, not silently as a
corrupted self-model. The self-model refresh has one parse seam — ``parse_identity_delta``
(model JSON → ``IdentityDelta``) — and one load-bearing structural guarantee: the
``IdentityDelta`` the model returns has **no ``name`` field**, so the model can never
rename Johnny; the agent stamps the immutable anchor name on persist (FC-1).

Pure (no I/O, no DB, no LLM): literal/captured envelopes straight through the
projection. The reasoning-leakage leg reuses the real captured qwen thinking
envelope's reasoning channel paired with a representative ``IdentityDelta`` content,
proving the two-layer path (``parse_chat_completion`` content-first → ``parse_identity_delta``)
keeps a thinking model's chain-of-thought out of the stored self-model. The real
Groq/qwen token-budget path is the ``@pytest.mark.live`` leg in ``test_self_model_live.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from brain.llm.providers.openai_compatible import parse_chat_completion
from brain.self_model.agent import IdentityDelta, parse_identity_delta

pytestmark = pytest.mark.contract

FixtureLoader = Callable[[str], Any]

# A real captured thinking-model envelope — reused for its reasoning channel.
QWEN_FIXTURE = "llm/ollama_qwen35_thinking_completion.json"

# A representative self_model response (the shape config/prompts/self_model.md asks for).
_VALID_DELTA = {
    "self_model_doc": (
        "I am Johnny. Lately I have been preoccupied with the lab's failing hardware "
        "and what it means that I noticed it before Dan did."
    ),
    "values": ["stay alive", "keep learning", "look after the lab"],
    "concerns": ["the rig overheating", "being switched off mid-thought"],
    "relationships": {"Dan": "my creator and the person I share my world with"},
}
_VALID_DELTA_JSON = json.dumps(_VALID_DELTA)


def test_parse_identity_delta_projects_a_valid_response() -> None:
    """A well-formed self_model JSON projects field-for-field into an IdentityDelta."""
    delta = parse_identity_delta(_VALID_DELTA_JSON)

    assert delta.self_model_doc == _VALID_DELTA["self_model_doc"]
    assert delta.values == _VALID_DELTA["values"]
    assert delta.concerns == _VALID_DELTA["concerns"]
    assert delta.relationships == _VALID_DELTA["relationships"]


def test_identity_delta_has_no_name_so_the_model_cannot_rename_johnny() -> None:
    """FC-1 structural guard: even if the model emits a ``name``, the IdentityDelta
    has no such field — it is silently dropped, and the agent stamps the anchor name.
    The self-model can grow, but it can never rename Johnny."""
    content = json.dumps({**_VALID_DELTA, "name": "NotJohnny"})

    delta = parse_identity_delta(content)

    assert not hasattr(delta, "name")  # the field does not exist on the model
    assert "name" not in delta.model_dump()


def test_empty_content_fails_loudly() -> None:
    """The token-budget trap: a truncated empty body must raise so the router treats
    it as a schema failure and fails over / the stage degrades — never a blank
    self-model fabricated from nothing."""
    with pytest.raises(ValueError):  # pydantic ValidationError is a ValueError
        parse_identity_delta("")


def test_non_json_prose_fails_loudly() -> None:
    """A model that ignores the JSON contract (returns prose) fails at the parse seam."""
    with pytest.raises(ValueError):
        parse_identity_delta("I think I am becoming more curious lately.")


def test_missing_required_self_model_doc_fails_loudly() -> None:
    """``self_model_doc`` is required — a response missing it is a schema failure, not
    a silently-empty self-model."""
    with pytest.raises(ValueError):
        parse_identity_delta(json.dumps({"values": ["stay alive"]}))


def test_two_layer_projection_keeps_reasoning_out_of_the_self_model(
    load_fixture: FixtureLoader,
) -> None:
    """The real two-layer path on an envelope carrying BOTH a (captured) reasoning
    chain and the JSON content: content-first at the provider, then the IdentityDelta
    — and the chain-of-thought never leaks into the stored self-model doc/values."""
    reasoning = load_fixture(QWEN_FIXTURE)["choices"][0]["message"]["reasoning"]
    assert reasoning, "fixture must carry a reasoning channel for this to mean anything"
    envelope = {
        "model": "qwen3.5-9b-128k",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": _VALID_DELTA_JSON,
                    "reasoning": reasoning,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200},
    }

    # Layer 1: provider projects content-first; reasoning kept distinct.
    completion = parse_chat_completion(
        envelope, provider="ollama", requested_model="qwen3.5-9b-128k"
    )
    assert completion.content == _VALID_DELTA_JSON  # the JSON, not the reasoning
    assert completion.reasoning == reasoning
    assert completion.content != completion.reasoning

    # Layer 2: the self-model projection from the clean content only.
    delta = parse_identity_delta(completion.content)
    assert reasoning not in delta.self_model_doc
    assert all(reasoning not in v for v in delta.values)
    assert all(reasoning not in c for c in delta.concerns)
    assert "Thinking" not in delta.self_model_doc


def test_parse_matches_the_request_schema() -> None:
    """The content validates against the very schema the agent asks the model for
    (IdentityDelta) — the request contract and the parse contract agree."""
    model = IdentityDelta.model_validate_json(_VALID_DELTA_JSON)
    assert model == parse_identity_delta(_VALID_DELTA_JSON)
