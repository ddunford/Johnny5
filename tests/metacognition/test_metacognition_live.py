"""Live guard that the metacognition review fits its token budget (TC-4.9 live leg).

The deterministic contract tests feed representative envelopes through the parser,
so they pass regardless of the real token budget. This is the one thing they can't
cover: that the **real** ``metacognition`` path returns a non-empty, parseable
``Review`` rather than a truncated empty body.

The ``metacognition`` role is cloud-first (Groq) with the local **qwen** reasoning
model as fallback. The token-budget trap (lessons.md) lives on the reasoning model:
under a JSON-schema instruction qwen emits a long reasoning preamble *before* the
JSON, so too small a ``metacognition_max_tokens`` truncates mid-reasoning
(``finish_reason="length"``, ``content=""``) → schema failover → the review silently
degrades to empty every sleep. This leg hits the qwen path directly at the configured
budget and fails if it truncates — same family as ``test_affect_live.py``.

Marked ``live`` (deselected unless ``--run-live``):

    ./ctl.sh test -m live --run-live tests/metacognition/test_metacognition_live.py
"""

from __future__ import annotations

import pytest

from brain.llm.base import Message
from brain.llm.providers.ollama import OllamaProvider
from brain.metacognition.agent import Metacognition, ReviewWindow, parse_review
from foundation.config import Settings

pytestmark = pytest.mark.live

_JSON_RESPONSE_FORMAT = {"type": "json_object"}
_TEMPERATURE = 0.5  # the analytical setting the agent uses

_WINDOW = ReviewWindow(
    goals_resolved=2,
    goals_abandoned=3,
    degraded_ticks=2,
    recent_goals=["satisfy curiosity about the failing rig", "reach out to Dan"],
    mood="restless, a little discouraged",
    drives="curiosity high, energy high (tired)",
)


async def test_live_metacognition_review_fits_the_token_budget() -> None:
    settings = Settings()
    agent = Metacognition()  # loads the real prompt; no router/DB needed to render
    messages = [
        Message(role="system", content=agent.prompt),
        Message(role="user", content=agent._render(_WINDOW)),
    ]

    provider = OllamaProvider(settings)
    try:
        completion = await provider.complete(
            messages,
            model=settings.local_reasoning_model,  # the qwen fallback — the trap-prone path
            temperature=_TEMPERATURE,
            max_tokens=settings.metacognition_max_tokens,
            response_format=_JSON_RESPONSE_FORMAT,
        )
    finally:
        await provider.aclose()

    assert completion.finish_reason != "length", (
        "metacognition completion truncated (finish_reason=length) — "
        "metacognition_max_tokens too small for qwen's reasoning preamble (see lessons.md)"
    )
    assert completion.content.strip(), (
        "metacognition completion empty — bump metacognition_max_tokens; qwen spent the "
        "budget on its reasoning channel before emitting the JSON Review"
    )
    review = parse_review(completion.content)
    assert review.reflection.strip()
