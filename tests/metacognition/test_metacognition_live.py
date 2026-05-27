"""Live guard that the metacognition review works on its PRIMARY path — Groq (TC-4.9).

The `metacognition` role is **cloud-first**: Groq (`llama-3.3-70b-versatile`) is the
production path and returns a clean `Review` JSON. This leg hits that path directly
and asserts the real request fits the token budget (`finish=stop`) and projects
through `parse_review`.

The local qwen fallback is a *reasoning* model whose chain-of-thought rambles non-
deterministically on structured output (can exhaust any sane `max_tokens`) — the
"tired" degradation (SPEC §10), not a token-guard target. The qwen-fallback path is
covered by the **deterministic** graceful-degradation test
`test_metacognition.py::test_tired_review_writes_no_notes` (tired → empty review, no
notes), plus the sleep-level all-LLM-unavailable test in `test_sleep_cycle.py`.
(`/no_think` for reliable local structured output is the Phase-6 item.)

Marked ``live`` (deselected unless ``--run-live``); real Groq call:

    ./ctl.sh test -m live --run-live tests/metacognition/test_metacognition_live.py
"""

from __future__ import annotations

import pytest

from brain.llm.base import Message
from brain.llm.providers.groq import GroqProvider
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


async def test_live_metacognition_review_on_groq_fits_the_budget_and_parses() -> None:
    settings = Settings()
    agent = Metacognition()  # loads the real prompt; no router/DB needed to render
    messages = [
        Message(role="system", content=agent.prompt),
        Message(role="user", content=agent._render(_WINDOW)),
    ]

    provider = GroqProvider(settings)  # the role's PRIMARY provider (production path)
    try:
        completion = await provider.complete(
            messages,
            model=settings.groq_model,
            temperature=_TEMPERATURE,
            max_tokens=settings.metacognition_max_tokens,
            response_format=_JSON_RESPONSE_FORMAT,
        )
    finally:
        await provider.aclose()

    assert completion.finish_reason != "length", (
        "metacognition truncated on Groq (finish=length) — metacognition_max_tokens too small"
    )
    assert completion.content.strip(), "metacognition completion empty on Groq"
    review = parse_review(completion.content)
    assert review.reflection.strip()
