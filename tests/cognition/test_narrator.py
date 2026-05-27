"""The Inner Narrator wired through the cycle (TASK-2.12 narrator leg, TC-2.5/2.6).

These exercise the **real** ``Narrator`` — its projection of a model response into
a persisted ``thought``, and its graceful degradation when every provider is
tired — without a live LLM. A canned router stands in for the inference layer
(the router contract is fixed: ``complete(role, messages, *, schema, temperature,
max_tokens) -> Completion``), so the assertion is on the Narrator's behaviour, not
on a model's prose (the model-output projection itself is locked by the contract
test, ``test_narrator_contract.py``). The real gemma4 narration is exercised under
``--run-live``.

DB backed (the Narrator persists every thought) → run in-network via
``./ctl.sh test``.
"""

from __future__ import annotations

from collections.abc import Sequence

from helpers.clock import FrozenClock
from helpers.cycle import PassthroughAttention, StubPerception, build_cycle
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.agents.narrator import Narrator, ThoughtRepository
from brain.llm.base import Completion, LLMUnavailableError, Message
from brain.workspace import Workspace, WorkspaceItem
from foundation.db import session_scope

_CANNED_THOUGHT = "I keep coming back to what Dan said about concision."


class _CannedRouter:
    """A router double that returns a fixed narrator JSON completion. Records the
    role/schema it was called with so the test can assert the Narrator used them."""

    def __init__(self, thought: str = _CANNED_THOUGHT) -> None:
        self._content = f'{{"thought": "{thought}"}}'
        self.calls: list[str] = []
        self.last_schema: type | None = None

    async def complete(
        self,
        role: str,
        messages: Sequence[Message],
        *,
        schema: type | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Completion:
        self.calls.append(role)
        self.last_schema = schema
        return Completion(content=self._content, provider="canned", model="canned-model")


class _TiredRouter:
    """A router double that is fully tired — every call raises (the terminal state
    the real router raises when no provider in the chain is available)."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, role: str, messages: Sequence[Message], **_kwargs: object
    ) -> Completion:
        self.calls += 1
        raise LLMUnavailableError(role)


def _contents() -> list[WorkspaceItem]:
    return [WorkspaceItem(kind="ambient", content="it is quiet at 09:00 UTC", salience=0.3)]


async def _thought_texts() -> list[str]:
    async with session_scope() as session:
        rows = await ThoughtRepository(session).list()
    return [row.text for row in rows]


async def test_narrate_projects_a_thought_and_persists_it(workspace_db: AsyncEngine) -> None:
    router = _CannedRouter()
    narrator = Narrator(router)  # type: ignore[arg-type]  # duck-typed router double

    thought = await narrator.narrate(contents=_contents())

    assert thought == _CANNED_THOUGHT
    # It went through the router on the narrator role, asking for the JSON schema.
    assert router.calls == ["narrator"]
    assert router.last_schema is not None
    # And the thought was persisted to the inner-monologue stream.
    assert await _thought_texts() == [_CANNED_THOUGHT]


async def test_narrate_degrades_to_none_when_every_provider_is_tired(
    workspace_db: AsyncEngine,
) -> None:
    router = _TiredRouter()
    narrator = Narrator(router)  # type: ignore[arg-type]

    thought = await narrator.narrate(contents=_contents())

    # No thought this tick — but no exception escaped (the heartbeat continues).
    assert thought is None
    assert router.calls == 1
    # Nothing persisted: a tired tick leaves no phantom thought.
    assert await _thought_texts() == []


async def test_narrator_thought_is_produced_and_broadcast_through_the_cycle(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """End-to-end through the loop: the cycle's narrate stage yields the thought,
    broadcasts it on the bus, and persists it — the visible stream of consciousness."""
    narrator = Narrator(_CannedRouter())  # type: ignore[arg-type]
    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(_contents()),
        attention=PassthroughAttention(),
        narration=narrator,
    )

    report = (await harness.run_ticks(1))[0]

    assert report.ok
    assert report.thought == _CANNED_THOUGHT
    # The thought was broadcast on the bus (the live stream the WS/REPL consume).
    thought_events = await workspace.recent_events(limit=50, type_filter="thought")
    assert [e.payload.get("text") for e in thought_events] == [_CANNED_THOUGHT]
    # ...and persisted to the thought log.
    assert await _thought_texts() == [_CANNED_THOUGHT]


async def test_cycle_treats_a_tired_narrator_as_graceful_not_a_failure(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """The distinction that matters for TC-2.6: a tired narrator returns ``None``
    (graceful degradation) — the tick stays ``ok`` with no stage error and simply
    skips the thought broadcast. (A stage that *raises* is the separate
    stage_errors path, covered in test_cycle.py.)"""
    narrator = Narrator(_TiredRouter())  # type: ignore[arg-type]
    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(_contents()),
        attention=PassthroughAttention(),
        narration=narrator,
    )

    report = (await harness.run_ticks(1))[0]

    assert report.thought is None
    assert report.ok  # tired is graceful — NOT a degraded stage
    assert report.stage_errors == {}
    # Nothing broadcast, nothing persisted — no phantom thought.
    assert await workspace.recent_events(limit=50, type_filter="thought") == []
    assert await _thought_texts() == []
