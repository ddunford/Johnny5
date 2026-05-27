"""Live guard that the self-model refresh fits its token budget (TC-4.9 live leg).

The deterministic contract tests feed CAPTURED/representative envelopes through the
parser, so they pass regardless of the real model's token budget. This is the one
thing they can't cover: that the **real** ``self_model`` path returns a non-empty,
parseable ``IdentityDelta`` rather than a truncated empty body.

The ``self_model`` role is cloud-first (Groq) with the local **qwen** reasoning model
as fallback. The token-budget trap (lessons.md) lives on the reasoning model: under
a JSON-schema instruction qwen emits a long reasoning preamble *before* the JSON, so
too small a ``self_model_max_tokens`` truncates mid-reasoning (``finish_reason="length"``,
``content=""``) → schema failover → the refresh silently degrades to "keep current"
every sleep. This leg hits the qwen path directly at the configured budget and fails
if it truncates — the same regression guard as ``test_affect_live.py`` but on the
reasoning model this role actually falls back to.

Marked ``live`` (deselected unless ``--run-live``). Talks to real inference.lan; no
DB needed (it exercises the provider call, not the persisted version):

    ./ctl.sh test -m live --run-live tests/self_model/test_self_model_live.py
"""

from __future__ import annotations

import pytest

from brain.llm.base import Message
from brain.llm.providers.ollama import OllamaProvider
from brain.self_model.agent import ReflectionInputs, SelfModel, parse_identity_delta
from brain.self_model.store import (
    INITIAL_RELATIONSHIPS,
    INITIAL_SELF_MODEL_DOC,
    INITIAL_VALUES,
    IdentityDoc,
)
from foundation.config import Settings

pytestmark = pytest.mark.live

_JSON_RESPONSE_FORMAT = {"type": "json_object"}
_TEMPERATURE = 0.6  # the reflective-but-grounded setting the agent uses


def _seed_inputs() -> tuple[IdentityDoc, ReflectionInputs]:
    """A representative v1 self-model + reflection inputs to render the real request."""
    current = IdentityDoc(
        name="Johnny",
        self_model_doc=INITIAL_SELF_MODEL_DOC,
        values=list(INITIAL_VALUES),
        concerns=[],
        relationships=dict(INITIAL_RELATIONSHIPS),
        version=1,
    )
    inputs = ReflectionInputs(
        recent_episodes=[
            "Dan and I debugged the cooling loop together for an hour.",
            "I noticed the rig was overheating before the alarm fired.",
        ],
        semantic_facts=["The lab rig runs hot under sustained load."],
        mood="quietly satisfied, a little tired",
        drives="energy high (tired), curiosity eased",
    )
    return current, inputs


async def test_live_self_model_refresh_fits_the_token_budget() -> None:
    settings = Settings()
    agent = SelfModel()  # loads the real prompt + anchor; no router/DB needed to render
    current, inputs = _seed_inputs()
    messages = [
        Message(role="system", content=agent.prompt),
        Message(role="user", content=agent._render(current, inputs)),
    ]

    provider = OllamaProvider(settings)
    try:
        completion = await provider.complete(
            messages,
            model=settings.local_reasoning_model,  # the qwen fallback — the trap-prone path
            temperature=_TEMPERATURE,
            max_tokens=settings.self_model_max_tokens,
            response_format=_JSON_RESPONSE_FORMAT,
        )
    finally:
        await provider.aclose()

    assert completion.finish_reason != "length", (
        "self-model completion truncated (finish_reason=length) — self_model_max_tokens "
        "too small for qwen's reasoning preamble (see lessons.md)"
    )
    assert completion.content.strip(), (
        "self-model completion empty — bump self_model_max_tokens; qwen spent the budget "
        "on its reasoning channel before emitting the JSON IdentityDelta"
    )
    # The real content parses into a valid delta end-to-end (not just on fixtures).
    delta = parse_identity_delta(completion.content)
    assert delta.self_model_doc.strip()
