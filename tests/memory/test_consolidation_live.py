"""Live guard that consolidation works on its PRIMARY path — Groq (TC-4.9 live leg).

The `consolidation` role is **cloud-first**: Groq (`llama-3.3-70b-versatile`) is the
production path and returns clean structured JSON. This leg hits that path directly
and asserts the real request fits the token budget (`finish=stop`, not `length`) and
projects through `parse_consolidation`.

Why Groq, not the local qwen fallback: qwen is a *reasoning* model whose chain-of-
thought is verbose and non-deterministic on a structured-output prompt — it can ramble
past any sane `max_tokens` (observed burning the full 4096 then `finish=length`),
which is the "tired" degradation SPEC §10 designs for (Groq down → simpler/local),
NOT something a token guard should assert clean JSON on. The qwen-fallback path is
therefore covered by **deterministic** graceful-degradation tests, not here:
`test_consolidator.py::test_run_without_a_router_writes_a_deterministic_fallback_fact`
and `::test_tired_router_attempts_the_llm_then_degrades_to_fallback`, plus the
sleep-level `test_sleep_cycle.py::test_sleep_completes_and_wakes_when_every_llm_is_unavailable`.
(Making qwen reliable for structured LOCAL output via `/no_think` is the Phase-6 item.)

Marked ``live`` (deselected unless ``--run-live``); real Groq call (small spend):

    ./ctl.sh test -m live --run-live tests/memory/test_consolidation_live.py
"""

from __future__ import annotations

import pytest

from brain.llm.base import Message
from brain.llm.providers.groq import GroqProvider
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


async def test_live_consolidation_summary_on_groq_fits_the_budget_and_parses() -> None:
    settings = Settings()
    # The exact request the consolidator sends: the real prompt + rendered cluster.
    agent = Consolidator(SemanticMemory())  # loads the real prompt; no router/DB to render
    messages = [
        Message(role="system", content=agent.prompt),
        Message(role="user", content=agent._render(_cluster())),
    ]

    provider = GroqProvider(settings)  # the role's PRIMARY provider (production path)
    try:
        completion = await provider.complete(
            messages,
            model=settings.groq_model,
            temperature=_TEMPERATURE,
            max_tokens=settings.consolidation_max_tokens,
            response_format=_JSON_RESPONSE_FORMAT,
        )
    finally:
        await provider.aclose()

    assert completion.finish_reason != "length", (
        "consolidation completion truncated on Groq (finish=length) — "
        "consolidation_max_tokens too small for the response"
    )
    assert completion.content.strip(), "consolidation completion empty on Groq"
    summary = parse_consolidation(completion.content)
    assert isinstance(summary, ConsolidationSummary)
    assert summary.subject.strip() and summary.object.strip()
