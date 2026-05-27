"""Live guard that the self-model refresh works on its PRIMARY path — Groq (TC-4.9).

The `self_model` role is **cloud-first**: Groq (`llama-3.3-70b-versatile`) is the
production path and returns a clean `IdentityDelta` JSON. This leg hits that path
directly and asserts the real request fits the token budget (`finish=stop`) and
projects through `parse_identity_delta`.

The local qwen fallback is a *reasoning* model whose chain-of-thought rambles non-
deterministically on structured output (can exhaust any sane `max_tokens`) — that's
the "tired" degradation (SPEC §10), not a token-guard target. The qwen-fallback path
is covered by the **deterministic** graceful-degradation test
`test_self_model.py::test_tired_refresh_keeps_the_current_version` (tired → no new
version), plus the sleep-level all-LLM-unavailable test in `test_sleep_cycle.py`.
(`/no_think` to make qwen reliable for local structured output is the Phase-6 item.)

Marked ``live`` (deselected unless ``--run-live``); real Groq call:

    ./ctl.sh test -m live --run-live tests/self_model/test_self_model_live.py
"""

from __future__ import annotations

import pytest

from brain.llm.base import Message
from brain.llm.providers.groq import GroqProvider
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


async def test_live_self_model_refresh_on_groq_fits_the_budget_and_parses() -> None:
    settings = Settings()
    agent = SelfModel()  # loads the real prompt + anchor; no router/DB needed to render
    current, inputs = _seed_inputs()
    messages = [
        Message(role="system", content=agent.prompt),
        Message(role="user", content=agent._render(current, inputs)),
    ]

    provider = GroqProvider(settings)  # the role's PRIMARY provider (production path)
    try:
        completion = await provider.complete(
            messages,
            model=settings.groq_model,
            temperature=_TEMPERATURE,
            max_tokens=settings.self_model_max_tokens,
            response_format=_JSON_RESPONSE_FORMAT,
        )
    finally:
        await provider.aclose()

    assert completion.finish_reason != "length", (
        "self-model completion truncated on Groq (finish=length) — self_model_max_tokens too small"
    )
    assert completion.content.strip(), "self-model completion empty on Groq"
    delta = parse_identity_delta(completion.content)
    assert delta.self_model_doc.strip()
