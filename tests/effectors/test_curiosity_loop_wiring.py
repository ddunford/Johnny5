"""TC-6b.4 (wiring half) — the cycle eases a drive ON the web-read consolidation.

The curiosity loop's defining rule: a web-read tool isn't "done" until its content
is remembered, so the satisfaction that eases the drive fires on the CONSOLIDATION,
not the raw fetch (SPEC §8). This drives a real ``CognitiveCycle`` through one tick
with a Deliberation that proposes a web tool, a registered fake web tool, and a fake
consolidator — and proves the gating: the goal settles ``success=True`` only when
the read consolidated, and ``success=False`` when it didn't (so Johnny keeps
looking). The full drive→goal→tool→remember→ease E2E is qa's TC-6b.4. DB/Redis-backed.
"""

from __future__ import annotations

from collections.abc import Sequence

from helpers.llm import CannedProvider, make_router
from pydantic import BaseModel, ConfigDict

from brain.affect.appraisal import Mood
from brain.agents.conscience import Conscience
from brain.agents.deliberation import Action, ActionOutcome, DeliberationResult
from brain.cycle import CognitiveCycle
from brain.drives.engine import DriveEvent, Urge
from brain.effectors.dispatch import EffectorDispatch
from brain.effectors.tools import DangerClass, ToolRegistry, ToolResult
from brain.effectors.web_consolidator import WebReadResult
from brain.goals.store import Goal
from brain.llm.routing import ModelStep
from brain.memory.episodic import Episode
from brain.memory.semantic import SemanticFact
from brain.workspace import Workspace, WorkspaceItem
from core.audit import AuditWriter

_ALLOW = '{"verdict": "allow", "reason": ""}'


async def _noop_sleep(_seconds: float) -> None:
    return None


class _NewsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str


class _FakeNewsTool:
    """A registered stand-in for the news tool returning one canned result."""

    name = "news"
    danger = DangerClass.NETWORK
    args_schema = _NewsArgs

    async def run(self, args: _NewsArgs) -> ToolResult:
        return ToolResult(
            success=True,
            output={
                "topic": args.topic,
                "results": [
                    {"title": "Rover lands", "url": "https://news.example/1", "snippet": "It did."}
                ],
                "count": 1,
            },
            summary="browsed news",
        )


class _ProposingDeliberation:
    """Proposes a `news` tool action for a curiosity goal; records settle outcomes."""

    def __init__(self) -> None:
        self._goal = Goal(id=1, source="curiosity", description="learn", priority=0.7)
        self.settled: list[bool] = []

    async def deliberate(
        self,
        *,
        urges: Sequence[Urge],
        mood: Mood | None,
        contents: Sequence[WorkspaceItem],
        now: object = None,
    ) -> DeliberationResult:
        action = Action(
            kind="news",
            goal_id=1,
            goal_source="curiosity",
            description="learn",
            tool="news",
            tool_args={"topic": "mars"},
        )
        return DeliberationResult(goal=self._goal, action=action)

    async def act(
        self, action: Action, goal: Goal, contents: Sequence[WorkspaceItem]
    ) -> ActionOutcome:  # pragma: no cover - tool actions never take this path
        return ActionOutcome(action_kind=action.kind, success=True, summary="")

    async def settle_tool_action(
        self, goal: Goal, *, summary: str, success: bool
    ) -> list[DriveEvent]:
        self.settled.append(success)
        return []


class _FakeConsolidator:
    """Returns a canned WebReadResult (consolidated) or None (nothing remembered)."""

    def __init__(self, *, result: WebReadResult | None) -> None:
        self._result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def consolidate_tool_result(
        self, tool: str, output: dict[str, object]
    ) -> WebReadResult | None:
        self.calls.append((tool, dict(output)))
        return self._result


def _web_read_result() -> WebReadResult:
    return WebReadResult(
        episode=Episode(
            id=1, kind="web_read", content="I read 'Rover lands' (https://news.example/1)."
        ),
        fact=SemanticFact(subject="Rover", predicate="landed", object="on Mars"),
        url="https://news.example/1",
        summarised=False,
    )


def _cycle(
    bus: Workspace, *, consolidator: _FakeConsolidator
) -> tuple[CognitiveCycle, _ProposingDeliberation]:
    provider = CannedProvider("ollama", content=_ALLOW)
    router = make_router(
        {"conscience": [ModelStep(provider="ollama", model="gemma4:e4b")]}, {"ollama": provider}
    )
    registry = ToolRegistry()
    registry.register(_FakeNewsTool())
    dispatch = EffectorDispatch(
        registry=registry, conscience=Conscience(router), audit=AuditWriter(), broadcaster=bus
    )
    deliberation = _ProposingDeliberation()
    cycle = CognitiveCycle(
        bus,
        deliberation=deliberation,
        dispatch=dispatch,
        web_consolidator=consolidator,  # type: ignore[arg-type]  # duck-typed consolidator double
        sleep_fn=_noop_sleep,
    )
    return cycle, deliberation


async def test_web_read_that_consolidates_eases_the_drive(bus: Workspace) -> None:
    consolidator = _FakeConsolidator(result=_web_read_result())
    cycle, deliberation = _cycle(bus, consolidator=consolidator)

    report = await cycle.tick()

    assert report.ok is True
    # The tool's result was handed to the consolidator (the "remember" step)...
    assert consolidator.calls and consolidator.calls[0][0] == "news"
    # ...and because it consolidated, the goal settled as a satisfied read (eases Curiosity).
    assert deliberation.settled == [True]


async def test_web_read_that_does_not_consolidate_does_not_ease(bus: Workspace) -> None:
    consolidator = _FakeConsolidator(result=None)  # nothing worth remembering
    cycle, deliberation = _cycle(bus, consolidator=consolidator)

    report = await cycle.tick()

    assert report.ok is True
    assert consolidator.calls  # consolidation was attempted
    # Reading without remembering eases nothing — success is gated on the consolidation.
    assert deliberation.settled == [False]
