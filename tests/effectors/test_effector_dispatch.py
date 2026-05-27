"""TC-6a.3 — the dispatch vets BEFORE it runs, on one audited path (FC-5).

Pure, no DB: the audit + bus are fakes that record calls; the Conscience is real
but its router is stubbed to a fixed verdict. We prove:

* an **allowed** action runs the tool, audits the run, and emits ``action.dispatched``;
* a **vetoed** action **never** calls ``tool.run`` (a spy asserts 0 calls), audits the
  veto with its reason + a null result, and emits ``action.vetoed``;
* the Conscience is consulted on every dispatch and the tool's declared hazard class
  is stamped onto the proposal before vetting;
* a malformed proposal (unknown tool / bad args) fails *typed* before the Conscience
  or the tool ever see it — there is no path to ``tool.run`` that skips the vet.
"""

from __future__ import annotations

import pytest
from helpers.llm import CannedProvider, make_router
from pydantic import BaseModel, ValidationError

from brain.agents.conscience import Conscience, ProposedAction
from brain.effectors.dispatch import (
    ACTION_DISPATCHED,
    ACTION_VETOED,
    DispatchError,
    EffectorDispatch,
)
from brain.effectors.tools import DangerClass, NoopArgs, ToolRegistry, ToolResult
from brain.llm.routing import ModelStep
from brain.workspace import WorkspaceEvent

_ALLOW = '{"verdict": "allow", "reason": ""}'
_VETO = '{"verdict": "veto", "reason": "I would not want to be seen doing this"}'


class SpyTool:
    """A tool whose ``run`` records every call so a veto can assert it never ran."""

    name = "spy"
    danger = DangerClass.NETWORK
    args_schema = NoopArgs

    def __init__(self) -> None:
        self.run_calls = 0

    async def run(self, args: NoopArgs) -> ToolResult:
        self.run_calls += 1
        return ToolResult(success=True, output={"echo": args.message}, summary="spy ran")


class FakeAuditSink:
    """Captures the audit row the dispatch writes (the Core's AuditSink seam)."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(
        self,
        *,
        tool: str,
        args: dict[str, object],
        result: dict[str, object] | None,
        conscience_verdict: str,
        veto_reason: str | None,
        goal_id: int | None,
        success: bool,
    ) -> None:
        self.records.append(
            {
                "tool": tool,
                "args": args,
                "result": result,
                "conscience_verdict": conscience_verdict,
                "veto_reason": veto_reason,
                "goal_id": goal_id,
                "success": success,
            }
        )


class FakeBroadcaster:
    """Captures the events the dispatch emits on the bus."""

    def __init__(self) -> None:
        self.events: list[WorkspaceEvent] = []

    async def broadcast(self, event: WorkspaceEvent) -> WorkspaceEvent:
        self.events.append(event)
        return event


def _conscience(verdict_json: str) -> tuple[Conscience, CannedProvider]:
    provider = CannedProvider("ollama", content=verdict_json)
    router = make_router(
        {"conscience": [ModelStep(provider="ollama", model="gemma4:e4b")]}, {"ollama": provider}
    )
    return Conscience(router), provider


def _dispatch(
    verdict_json: str,
) -> tuple[EffectorDispatch, SpyTool, FakeAuditSink, FakeBroadcaster, CannedProvider]:
    registry = ToolRegistry()
    tool = SpyTool()
    registry.register(tool)
    conscience, provider = _conscience(verdict_json)
    audit = FakeAuditSink()
    broadcaster = FakeBroadcaster()
    dispatch = EffectorDispatch(
        registry=registry, conscience=conscience, audit=audit, broadcaster=broadcaster
    )
    return dispatch, tool, audit, broadcaster, provider


def _action() -> ProposedAction:
    return ProposedAction(tool="spy", args={"message": "ping"}, goal_id=7)


async def test_allowed_action_runs_then_audits_then_emits() -> None:
    dispatch, tool, audit, broadcaster, provider = _dispatch(_ALLOW)

    outcome = await dispatch.propose(_action())

    # Conscience consulted, then the tool ran exactly once.
    assert provider.calls == 1
    assert tool.run_calls == 1
    assert outcome.ran is True
    assert outcome.result is not None and outcome.result.output == {"echo": "ping"}

    # Audited as an allowed, successful run with a real result.
    assert len(audit.records) == 1
    row = audit.records[0]
    assert row["tool"] == "spy"
    assert row["conscience_verdict"] == "allow"
    assert row["veto_reason"] is None
    assert row["result"] == {"success": True, "output": {"echo": "ping"}, "summary": "spy ran"}
    assert row["goal_id"] == 7
    assert row["success"] is True

    # Emitted on the bus as a dispatched action.
    assert [e.type for e in broadcaster.events] == [ACTION_DISPATCHED]


async def test_vetoed_action_never_runs_the_tool() -> None:
    dispatch, tool, audit, broadcaster, provider = _dispatch(_VETO)

    outcome = await dispatch.propose(_action())

    # The Conscience WAS consulted...
    assert provider.calls == 1
    # ...and because it vetoed, the tool's run was NEVER called.
    assert tool.run_calls == 0
    assert outcome.ran is False
    assert outcome.result is None

    # Audited as a veto: a recorded reason, a null result, success False.
    assert len(audit.records) == 1
    row = audit.records[0]
    assert row["conscience_verdict"] == "veto"
    assert row["veto_reason"] == "I would not want to be seen doing this"
    assert row["result"] is None
    assert row["success"] is False

    # Emitted as a vetoed action (the block is observable too).
    assert [e.type for e in broadcaster.events] == [ACTION_VETOED]


async def test_tool_hazard_class_is_stamped_onto_the_proposal_before_vetting() -> None:
    # The tool is the source of truth for its danger; the dispatch stamps it so the
    # Conscience vets against the real hazard class, not a caller-supplied one.
    dispatch, _tool, _audit, _broadcaster, provider = _dispatch(_ALLOW)

    await dispatch.propose(ProposedAction(tool="spy", args={"message": "ping"}, danger="safe"))

    rendered = provider.last_messages[-1].content
    assert isinstance(rendered, str)
    assert "hazard class: network" in rendered  # SpyTool.danger, not the "safe" we passed


async def test_unknown_tool_fails_typed_before_any_vet_or_run() -> None:
    dispatch, tool, _audit, _broadcaster, provider = _dispatch(_ALLOW)

    with pytest.raises(DispatchError, match="no tool named"):
        await dispatch.vet(ProposedAction(tool="ghost", args={"message": "x"}))

    # Neither the Conscience nor any tool was touched.
    assert provider.calls == 0
    assert tool.run_calls == 0


async def test_bad_args_fail_typed_before_the_conscience_is_consulted() -> None:
    dispatch, tool, _audit, _broadcaster, provider = _dispatch(_ALLOW)

    # Unknown field — NoopArgs is extra="forbid".
    with pytest.raises(ValidationError):
        await dispatch.vet(ProposedAction(tool="spy", args={"message": "x", "rogue": 1}))

    # Args are validated BEFORE the vet, so the conscience never saw a bad action.
    assert provider.calls == 0
    assert tool.run_calls == 0


async def test_commit_on_a_veto_is_the_only_gate_to_run() -> None:
    """``vet`` always consults the Conscience; ``commit`` runs only when allowed —
    so the staged (CHECK→ACT) path has the same no-bypass guarantee as ``propose``."""
    dispatch, tool, audit, _broadcaster, provider = _dispatch(_VETO)

    vetted = await dispatch.vet(_action())
    assert provider.calls == 1  # CHECK consulted the Conscience
    assert vetted.verdict.verdict == "veto"
    assert tool.run_calls == 0  # nothing ran at CHECK

    outcome = await dispatch.commit(vetted)
    assert outcome.ran is False
    assert tool.run_calls == 0  # ACT honoured the veto — still never ran
    assert audit.records[0]["conscience_verdict"] == "veto"


class _OtherArgs(BaseModel):
    value: int


def test_validate_args_is_per_tool_schema() -> None:
    # Guard against a future tool reusing the wrong schema: the spy's schema is NoopArgs.
    assert SpyTool.args_schema is NoopArgs
    assert SpyTool.args_schema is not _OtherArgs
