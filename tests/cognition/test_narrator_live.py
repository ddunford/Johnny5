"""Live end-to-end check that the Inner Narrator actually narrates (TC-2.6 live leg).

The deterministic 2.12/2.13 tests use canned routers, so they pass regardless of
the real model's token budget. This is the one thing they can't cover: that the
**real** gemma4 narrator, given a realistic workspace, returns a non-empty thought
rather than degrading to ``None``. It is the regression guard for the
``_MAX_TOKENS`` bug found during Phase 2 — under the reflective persona gemma4
emits a reasoning preamble, and too small a completion budget leaves no room for
the JSON ``{"thought": ...}`` (empty content → schema failover → tired → no
monologue).

Marked ``live`` (deselected unless ``--run-live``) and DB-backed (the Narrator
persists each thought), so run in-network:

    ./ctl.sh test -m live --run-live tests/cognition/test_narrator_live.py
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.agents.narrator import Narrator, ThoughtRepository
from brain.llm.call_logger import CallLogRecord
from brain.llm.providers.ollama import OLLAMA_PROVIDER_NAME, OllamaProvider
from brain.llm.router import LLMRouter
from brain.llm.routing import ModelStep, RoutingConfig
from brain.workspace import WorkspaceItem
from foundation.config import Settings
from foundation.db import session_scope

pytestmark = pytest.mark.live


class _MemoryLogger:
    """In-memory call logger so the live router needs no DB for the call log
    (the thought itself still persists via the Narrator)."""

    def __init__(self) -> None:
        self.records: list[CallLogRecord] = []

    async def record(self, entry: CallLogRecord) -> None:
        self.records.append(entry)


async def test_live_narrator_emits_a_real_thought(workspace_db: AsyncEngine) -> None:
    settings = Settings()
    router = LLMRouter(
        providers={OLLAMA_PROVIDER_NAME: OllamaProvider(settings)},
        routing=RoutingConfig(
            roles={
                "narrator": [
                    ModelStep(provider=OLLAMA_PROVIDER_NAME, model=settings.local_fast_model)
                ]
            }
        ),
        call_logger=_MemoryLogger(),
    )
    narrator = Narrator(router)
    contents = [
        WorkspaceItem(
            kind="input", content="Dan said: I just adopted a dog named Pixel.", salience=0.9
        ),
        WorkspaceItem(kind="memory", content="Dan prefers concise answers", salience=0.5),
        WorkspaceItem(
            kind="ambient", content="All quiet — it's 09:00 UTC, the host is idling.", salience=0.1
        ),
    ]
    try:
        thought = await narrator.narrate(contents=contents)

        # The headline deliverable: a real, non-empty first-person thought. If this
        # is None, the narrator went 'tired' — almost certainly _MAX_TOKENS is too
        # small for gemma4's reasoning preamble (see the Phase-2 bug report).
        assert thought is not None, (
            "narrator produced no thought — tired/degraded; check _MAX_TOKENS vs "
            "gemma4's reasoning budget under the narrator persona"
        )
        assert thought.strip()
        # No JSON envelope or reasoning scaffolding leaked into the thought.
        assert not thought.lstrip().startswith("{")
        assert "Thinking Process" not in thought

        # And it was persisted to the inner-monologue stream.
        async with session_scope() as session:
            rows = await ThoughtRepository(session).list()
        assert [row.text for row in rows] == [thought]
    finally:
        await router.aclose()
