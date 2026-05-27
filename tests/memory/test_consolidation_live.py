"""Live guard that consolidation summarisation fits its token budget (TC-4.9 live leg).

The deterministic contract tests feed representative envelopes through the parser,
so they pass regardless of the real token budget. This is the one thing they can't
cover: that the **real** ``consolidation`` path returns a non-empty, parseable
``ConsolidationSummary`` rather than a truncated empty body.

The ``consolidation`` role is cloud-first (Groq) with the local **qwen** reasoning
model as fallback. The token-budget trap (lessons.md) lives on the reasoning model:
under a JSON-schema instruction qwen emits a long reasoning preamble *before* the
JSON, so too small a ``consolidation_max_tokens`` truncates mid-reasoning
(``finish_reason="length"``, ``content=""``) → schema failover → consolidation
silently degrades to the deterministic fallback every sleep. This leg hits the qwen
path directly at the configured budget and fails if it truncates.

Marked ``live`` (deselected unless ``--run-live``):

    ./ctl.sh test -m live --run-live tests/memory/test_consolidation_live.py
"""

from __future__ import annotations

import pytest

from brain.llm.base import Message
from brain.llm.providers.ollama import OllamaProvider
from brain.memory.consolidator import ConsolidationSummary, Consolidator, parse_consolidation
from brain.memory.episodic import EpisodeRow
from brain.memory.semantic import SemanticMemory
from foundation.config import Settings

pytestmark = pytest.mark.live

_JSON_RESPONSE_FORMAT = {"type": "json_object"}
_TEMPERATURE = 0.4  # the faithful-distillation setting the consolidator uses


def _cluster() -> list[EpisodeRow]:
    """A representative episode cluster to render the real consolidation request."""
    return [
        EpisodeRow(id=1, kind="observation", content="The lab lights flickered twice."),
        EpisodeRow(id=2, kind="observation", content="The server fan spun up loud under load."),
        EpisodeRow(id=3, kind="reflection", content="I suspect the rig is overheating."),
    ]


async def test_live_consolidation_summary_fits_the_token_budget() -> None:
    settings = Settings()
    # Build the exact request the consolidator sends: the real prompt + rendered cluster.
    agent = Consolidator(SemanticMemory())  # loads the real prompt; no router/DB to render
    messages = [
        Message(role="system", content=agent.prompt),
        Message(role="user", content=agent._render(_cluster())),
    ]

    provider = OllamaProvider(settings)
    try:
        completion = await provider.complete(
            messages,
            model=settings.local_reasoning_model,  # the qwen fallback — the trap-prone path
            temperature=_TEMPERATURE,
            max_tokens=settings.consolidation_max_tokens,
            response_format=_JSON_RESPONSE_FORMAT,
        )
    finally:
        await provider.aclose()

    assert completion.finish_reason != "length", (
        "consolidation completion truncated (finish_reason=length) — "
        "consolidation_max_tokens too small for qwen's reasoning preamble (see lessons.md)"
    )
    assert completion.content.strip(), (
        "consolidation completion empty — bump consolidation_max_tokens; qwen spent the "
        "budget on its reasoning channel before emitting the JSON summary"
    )
    summary = parse_consolidation(completion.content)
    assert isinstance(summary, ConsolidationSummary)
    assert summary.subject.strip() and summary.object.strip()
