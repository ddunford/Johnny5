"""TC-6a.9 (integration) — the cycle's CHECK + ACT run the dispatch in place (FC-7).

The Conscience now fills CHECK and the effector dispatch fills ACT. This drives a
**real** :class:`CognitiveCycle` through one tick with a Deliberation that proposes
the inert ``noop`` tool, and proves the fill is in-place and benign:

* the tick completes with **no degraded stage** — the new CHECK/ACT don't perturb
  the heartbeat;
* an allowed action is vetted at CHECK, run at ACT, and written to ``action_log``
  via the Core writer, surfaced on the bus as ``action.dispatched``;
* a vetoed action is recorded but the tool never runs (no result), and the tick is
  still clean.

Only the Conscience's router is stubbed; the dispatch, registry, Core AuditWriter,
and Workspace bus are the real wiring. DB-/Redis-backed (in-network).
"""

from __future__ import annotations

from collections.abc import Sequence

from helpers.llm import CannedProvider, make_router
from sqlalchemy import text

from brain.affect.appraisal import Mood
from brain.agents.conscience import Conscience
from brain.agents.deliberation import Action, ActionOutcome, DeliberationResult
from brain.cycle import CognitiveCycle
from brain.drives.engine import DriveEvent, Urge
from brain.effectors.dispatch import EffectorDispatch
from brain.effectors.tools import default_tool_registry
from brain.goals.store import Goal
from brain.llm.routing import ModelStep
from brain.workspace import Workspace, WorkspaceItem
from core.audit import AuditWriter
from foundation.db import session_scope

_ALLOW = '{"verdict": "allow", "reason": ""}'
_VETO = '{"verdict": "veto", "reason": "I would rather not"}'


async def _noop_sleep(_seconds: float) -> None:
    return None


class ToolProposingDeliberation:
    """A Deliberation double that always proposes the inert ``noop`` tool action.

    Production Deliberation ships with an EMPTY ``tool_actions`` map (it won't
    auto-fire ``noop`` on the heartbeat), so to exercise the dispatch through the
    cycle we hand it a goal that maps to a tool action — exactly the seam 6b uses.
    """

    def __init__(self, message: str = "explore") -> None:
        self._goal = Goal(id=None, source="curiosity", description="learn something", priority=0.6)
        self._message = message
        self.settled: list[tuple[str, bool]] = []

    async def deliberate(
        self,
        *,
        urges: Sequence[Urge],
        mood: Mood | None,
        contents: Sequence[WorkspaceItem],
        now: object = None,
    ) -> DeliberationResult:
        action = Action(
            kind="noop",
            goal_id=self._goal.id,
            goal_source=self._goal.source,
            description=self._goal.description,
            tool="noop",
            tool_args={"message": self._message},
        )
        return DeliberationResult(goal=self._goal, action=action)

    async def act(
        self, action: Action, goal: Goal, contents: Sequence[WorkspaceItem]
    ) -> ActionOutcome:  # pragma: no cover - tool actions never take this path
        return ActionOutcome(action_kind=action.kind, success=True, summary="")

    async def settle_tool_action(
        self, goal: Goal, *, summary: str, success: bool
    ) -> list[DriveEvent]:
        self.settled.append((summary, success))
        return []


def _cycle(bus: Workspace, verdict_json: str) -> tuple[CognitiveCycle, ToolProposingDeliberation]:
    provider = CannedProvider("ollama", content=verdict_json)
    router = make_router(
        {"conscience": [ModelStep(provider="ollama", model="gemma4:e4b")]}, {"ollama": provider}
    )
    dispatch = EffectorDispatch(
        registry=default_tool_registry(),
        conscience=Conscience(router),
        audit=AuditWriter(),
        broadcaster=bus,
    )
    deliberation = ToolProposingDeliberation()
    cycle = CognitiveCycle(
        bus,
        deliberation=deliberation,
        dispatch=dispatch,
        sleep_fn=_noop_sleep,
    )
    return cycle, deliberation


async def _action_log_rows() -> list[dict[str, object]]:
    async with session_scope() as session:
        result = await session.execute(
            text("SELECT tool, result, conscience_verdict, success FROM action_log ORDER BY id")
        )
        return [dict(row._mapping) for row in result]


async def _event_types() -> list[str]:
    async with session_scope() as session:
        result = await session.execute(text("SELECT type FROM workspace_event ORDER BY id"))
        return [row._mapping["type"] for row in result]


async def test_allowed_tool_action_runs_through_check_and_act_without_degrading_the_tick(
    bus: Workspace,
) -> None:
    cycle, deliberation = _cycle(bus, _ALLOW)

    report = await cycle.tick()

    # The heartbeat is intact — CHECK + ACT did not degrade any stage.
    assert report.stage_errors == {}
    assert report.ok is True

    # CHECK vetted, ACT ran the tool, the Core wrote the audit row.
    rows = await _action_log_rows()
    assert len(rows) == 1
    assert rows[0]["tool"] == "noop"
    assert rows[0]["conscience_verdict"] == "allow"
    assert rows[0]["result"] is not None
    assert rows[0]["success"] is True

    # Surfaced on the bus (the /api/v1/audit path).
    assert "action.dispatched" in await _event_types()
    # The goal was settled as a successful tool action.
    assert deliberation.settled == [("noop echoed: 'explore'", True)]


async def test_vetoed_tool_action_is_recorded_but_never_runs_and_tick_stays_clean(
    bus: Workspace,
) -> None:
    cycle, deliberation = _cycle(bus, _VETO)

    report = await cycle.tick()

    assert report.ok is True  # a veto is a normal outcome, not a degraded tick

    rows = await _action_log_rows()
    assert len(rows) == 1
    assert rows[0]["conscience_verdict"] == "veto"
    assert rows[0]["result"] is None  # the tool never ran
    assert rows[0]["success"] is False

    assert "action.vetoed" in await _event_types()
    # A vetoed action eases nothing — settled with success=False.
    assert deliberation.settled == [("vetoed: I would rather not", False)]


async def test_repeated_ticks_keep_appending_one_row_each(bus: Workspace) -> None:
    cycle, _deliberation = _cycle(bus, _ALLOW)

    for _ in range(3):
        report = await cycle.tick()
        assert report.ok is True

    rows = await _action_log_rows()
    assert len(rows) == 3
    assert all(r["conscience_verdict"] == "allow" for r in rows)
