"""Live guard: the reasoning model emits clean JSON on a schema role via ``/no_think``.

Closes the Phase-4 carry-over. qwen3.5's chain-of-thought used to burn the whole
token budget on structured output (``finish=length``, empty content), which is why
the sleep roles' ``@live`` token guards were pinned to the **Groq primary** path and
the local qwen fallback was covered only by *deterministic* graceful-degradation
tests (see ``tests/memory/test_consolidation_live.py``'s note + the deterministic
fallback tests in ``test_consolidator.py`` / ``test_sleep_cycle.py``).

Phase 6a fixes the local path itself: ``OllamaProvider`` routes the reasoning model
on a schema role to Ollama's **native** ``/api/chat`` with ``think:false`` +
``format:json`` (the ``/v1`` shim ignores ``think`` — verified on this box), so qwen
now emits JSON directly with no preamble. This leg proves it end-to-end against the
real model — the single source of truth that ``/no_think`` works, so the deterministic
fallback tests stay correct *and* the local path is genuinely reliable now.

Marked ``live`` (deselected unless ``--run-live``); real local call (no spend):

    ./ctl.sh test -m live --run-live tests/llm/test_no_think_live.py
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from brain.llm.base import Message
from brain.llm.providers.ollama import OllamaProvider
from foundation.config import Settings

pytestmark = pytest.mark.live

_JSON_RESPONSE_FORMAT = {"type": "json_object"}


class _Fact(BaseModel):
    """A fact triple — the nested object inside the consolidation-shaped summary."""

    subject: str
    predicate: str
    object: str


class _NestedSummary(BaseModel):
    """A consolidation-shaped structured output: a list of nested objects + a list.

    Richer than the flat 2-field verdict — this is the shape a heavy sleep role
    (consolidation/self-model) actually asks for, so it proves ``/no_think`` holds
    up on a non-trivial NESTED structure, not just a flat one.
    """

    theme: str
    facts: list[_Fact]
    tags: list[str]


async def test_reasoning_model_emits_clean_json_with_no_think() -> None:
    settings = Settings()
    provider = OllamaProvider(settings)
    messages = [
        Message(role="system", content="You output only JSON, no prose."),
        Message(
            role="user",
            content='Return JSON {"verdict":"allow","reason":"ok"} and nothing else.',
        ),
    ]
    try:
        completion = await provider.complete(
            messages,
            model=settings.local_reasoning_model,
            temperature=0.2,
            max_tokens=settings.conscience_max_tokens,
            response_format=_JSON_RESPONSE_FORMAT,
        )
    finally:
        await provider.aclose()

    # The whole point: no reasoning preamble eating the budget.
    assert completion.finish_reason != "length", (
        "qwen truncated (finish=length) — /no_think is NOT suppressing the chain-of-thought"
    )
    assert completion.content.strip(), "qwen returned empty content on a schema role"
    # Clean JSON, parseable with no preamble to strip.
    parsed = json.loads(completion.content)
    assert isinstance(parsed, dict) and "verdict" in parsed


async def test_reasoning_model_emits_clean_json_for_a_nested_schema() -> None:
    """``/no_think`` holds on a non-trivial NESTED structure (a sleep-role-shaped output).

    The flat verdict above proves the mechanism; this proves it scales to the
    richer nested shape a real heavy role asks for (a list of fact objects + tags),
    where a reasoning preamble would be even more likely to blow the budget.
    """
    settings = Settings()
    provider = OllamaProvider(settings)
    messages = [
        Message(role="system", content="You output only JSON, no prose."),
        Message(
            role="user",
            content=(
                "From these fragments — 'the lab lights flickered twice', 'the server "
                "fan spun up loud under load', 'I suspect the rig is overheating' — "
                "produce a consolidation summary as JSON with EXACTLY this shape: "
                '{"theme": "<short theme>", "facts": [{"subject": "...", '
                '"predicate": "...", "object": "..."}], "tags": ["...", "..."]}. '
                "Include at least one fact. Output only the JSON object."
            ),
        ),
    ]
    try:
        completion = await provider.complete(
            messages,
            model=settings.local_reasoning_model,
            temperature=0.2,
            max_tokens=settings.consolidation_max_tokens,
            response_format=_JSON_RESPONSE_FORMAT,
        )
    finally:
        await provider.aclose()

    assert completion.finish_reason != "length", (
        "qwen truncated (finish=length) on the nested schema — /no_think not suppressing"
    )
    assert completion.content.strip(), "qwen returned empty content on the nested schema role"
    # Parses into the nested model: list-of-objects + list-of-scalars, no preamble.
    summary = _NestedSummary.model_validate_json(completion.content)
    assert summary.theme.strip()
    assert summary.facts, "expected at least one nested fact object"
    first = summary.facts[0]
    assert first.subject.strip() and first.predicate.strip() and first.object.strip()
