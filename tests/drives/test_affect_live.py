"""Live end-to-end guard that the affect LLM appraisal fits its token budget.

The deterministic 3.11 contract tests feed CAPTURED envelopes through the parser,
so they pass regardless of the real model's token budget. This is the one thing
they can't cover: that the **real** ``affect`` role (gemma4:e4b), under the
appraisal persona, returns a non-empty JSON appraisal rather than a truncated
empty body.

This is the regression guard for the ``affect_max_tokens`` trap (same family as
the Phase-2 narrator ``_MAX_TOKENS`` bug, lessons.md): under the appraisal persona
+ ``json_object``, gemma4 emits a ~500-token reasoning preamble *before* the small
JSON object. Too small a completion budget truncates mid-reasoning
(``finish_reason="length"``, ``content=""``) → schema failover → the LLM appraisal
silently degrades to rule-based on **every** interaction. This test fails when
``affect_max_tokens`` is too small and passes once it has headroom.

Marked ``live`` (deselected unless ``--run-live``). Talks to real inference.lan; no
DB needed (it exercises the provider call, not the persisted mood):

    ./ctl.sh test -m live --run-live tests/drives/test_affect_live.py
"""

from __future__ import annotations

import pytest

from brain.affect.agent import Affect
from brain.affect.appraisal import parse_appraisal
from brain.llm.base import Message
from brain.llm.providers.ollama import OllamaProvider
from foundation.config import Settings

pytestmark = pytest.mark.live

_SITUATION = "Dan said: I just adopted a dog named Pixel."
_JSON_RESPONSE_FORMAT = {"type": "json_object"}
# The appraisal role is run at low temperature (stable, not creative).
_TEMPERATURE = 0.4


async def test_live_affect_appraisal_fits_the_token_budget() -> None:
    settings = Settings()
    # Build the exact request the agent sends: the real prompt + rendered event.
    agent = Affect()  # no router/DB needed — just the prompt + render
    messages = [
        Message(role="system", content=agent.prompt),
        Message(role="user", content=agent._render(_SITUATION)),
    ]

    provider = OllamaProvider(settings)
    try:
        completion = await provider.complete(
            messages,
            model=settings.local_fast_model,
            temperature=_TEMPERATURE,
            max_tokens=settings.affect_max_tokens,
            response_format=_JSON_RESPONSE_FORMAT,
        )
    finally:
        await provider.aclose()

    # The token-budget regression guard: gemma4's reasoning preamble must NOT
    # consume the whole completion budget.
    assert completion.finish_reason != "length", (
        "affect completion truncated (finish_reason=length) — affect_max_tokens too "
        "small for gemma4's reasoning preamble (see lessons.md)"
    )
    assert completion.content.strip(), (
        "affect completion empty — bump affect_max_tokens; gemma4 spent the budget "
        "on its reasoning channel before emitting the JSON appraisal"
    )
    # And the real content parses into a valid appraisal (the projection works
    # end-to-end on real tokens, not just on captured fixtures).
    appraisal = parse_appraisal(completion.content)
    assert -1.0 <= appraisal.goal_congruence <= 1.0
    assert 0.0 <= appraisal.novelty <= 1.0
    assert 0.0 <= appraisal.agency <= 1.0
    assert 0.0 <= appraisal.certainty <= 1.0
