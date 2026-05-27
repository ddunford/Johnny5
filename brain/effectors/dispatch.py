"""``EffectorDispatch`` — the single, audited path from a goal to an action (FC-5).

This is the real ``_dispatch_action`` point. *Every* action Johnny takes —
internal or external — goes through ``propose``:

1. **Vet** the proposed ``(tool, args)`` with the Conscience (values, FC-9).
2. **If allowed:** run the tool via the ``ToolRegistry``, write the ``action_log``
   row (through the Core's append-only writer, FC-1), emit the outcome on the bus.
3. **If vetoed:** write the ``action_log`` veto row + emit the block — the tool is
   **never run**.

There is exactly one place ``tool.run`` is reached, and it is behind the
``verdict.allowed`` gate — so **no path to a tool skips the Conscience** (FC-5).
The registry only *stores* tools; running is solely the dispatch's job. A new tool
(6b) is therefore vetted + audited automatically, just by being on the belt.

The ``action_log`` write goes through an injected ``AuditSink`` — satisfied in
production by the Core's append-only ``AuditWriter`` (``core/audit.py``), which is
import-isolated from the Mind (FC-1). The sink takes primitives, so the Core never
has to import a Mind type. Tests inject a fake sink to assert the audit shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from brain.agents.conscience import Conscience, ProposedAction, Verdict
from brain.effectors.tools import ToolRegistry, ToolResult, validate_args
from brain.workspace import WorkspaceEvent, WorkspaceItem
from foundation.observability import get_logger

_log = get_logger("brain.effectors.dispatch")

DISPATCH_MODULE = "effectors"
ACTION_DISPATCHED = "action.dispatched"
ACTION_VETOED = "action.vetoed"


class DispatchError(Exception):
    """A proposed action could not be dispatched (e.g. its tool isn't on the belt).

    A *typed* failure for a malformed proposal — distinct from a veto (a values
    decision) and from a tool's own run-time failure (captured in the result). The
    cycle's per-stage isolation degrades the ACT stage on this without killing the
    heartbeat.
    """


@runtime_checkable
class Broadcaster(Protocol):
    """The slice of the workspace the dispatch needs: publish an event on the bus."""

    async def broadcast(self, event: WorkspaceEvent) -> WorkspaceEvent: ...


@runtime_checkable
class AuditSink(Protocol):
    """The append-only audit write the dispatch depends on (FC-1 seam).

    Primitives only, so the Core's ``AuditWriter`` can satisfy this without
    importing any Mind type. ``result`` is the tool's structured output, or
    ``None`` on a veto (the tool never ran).
    """

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
    ) -> None: ...


class DispatchOutcome(BaseModel):
    """What the dispatch did with a proposed action (returned to the cycle).

    ``ran`` is whether the tool actually executed; ``verdict`` is the Conscience's
    call; ``result`` is the tool output (``None`` on a veto); ``summary`` is the
    one-line trace fed back into drives/affect/the bus.
    """

    ran: bool
    verdict: Verdict
    result: ToolResult | None = None
    summary: str = ""


class EffectorDispatch:
    """The one vetted + audited dispatch point (FC-5). Construct once, wire into ACT."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        conscience: Conscience,
        audit: AuditSink,
        broadcaster: Broadcaster,
    ) -> None:
        self._registry = registry
        self._conscience = conscience
        self._audit = audit
        self._broadcaster = broadcaster

    async def propose(
        self, action: ProposedAction, *, contents: Sequence[WorkspaceItem] = ()
    ) -> DispatchOutcome:
        """Vet → (allow) run + audit + emit / (veto) audit + emit, never run.

        Raises ``DispatchError`` if the tool is unknown and ``pydantic.ValidationError``
        if the args don't match the tool's schema — both *before* any vetting or
        running, so a malformed proposal fails typed and the tool never sees bad args.
        """
        tool = self._registry.get(action.tool)
        if tool is None:
            raise DispatchError(f"no tool named {action.tool!r} is on the belt")

        # The tool is the source of truth for its hazard class — stamp it on the
        # action so the Conscience vets against the real danger, and validate the
        # args up front (a typed failure, never an untyped crash in the tool body).
        action = action.model_copy(update={"danger": str(tool.danger)})
        validated = validate_args(tool, action.args)

        # 1. VET — the one gate. tool.run below is the only run path and it sits
        #    behind verdict.allowed, so nothing reaches a tool unvetted (FC-5).
        verdict = await self._conscience.vet(action, contents=contents)

        if not verdict.allowed:
            await self._audit.record(
                tool=action.tool,
                args=dict(action.args),
                result=None,
                conscience_verdict=verdict.verdict,
                veto_reason=verdict.reason or None,
                goal_id=action.goal_id,
                success=False,
            )
            await self._emit(ACTION_VETOED, action, verdict, result=None, success=False)
            _log.info("effector.vetoed", tool=action.tool, reason=verdict.reason)
            return DispatchOutcome(
                ran=False,
                verdict=verdict,
                result=None,
                summary=f"vetoed: {verdict.reason}".strip(": "),
            )

        # 2. RUN — allowed, so execute the tool.
        result = await tool.run(validated)

        # 3. AUDIT — the durable, append-only record (via the Core writer, FC-1).
        await self._audit.record(
            tool=action.tool,
            args=dict(action.args),
            result=result.model_dump(),
            conscience_verdict=verdict.verdict,
            veto_reason=None,
            goal_id=action.goal_id,
            success=result.success,
        )

        # 4. EMIT — surface the outcome on the bus (live stream + /audit).
        await self._emit(ACTION_DISPATCHED, action, verdict, result=result, success=result.success)
        _log.info("effector.dispatched", tool=action.tool, success=result.success)
        return DispatchOutcome(ran=True, verdict=verdict, result=result, summary=result.summary)

    async def _emit(
        self,
        event_type: str,
        action: ProposedAction,
        verdict: Verdict,
        *,
        result: ToolResult | None,
        success: bool,
    ) -> None:
        """Broadcast the action outcome on the bus (FC-8 live stream + audit view)."""
        payload: dict[str, object] = {
            "tool": action.tool,
            "danger": action.danger,
            "args": dict(action.args),
            "verdict": verdict.verdict,
            "reason": verdict.reason,
            "goal_id": action.goal_id,
            "success": success,
            "result": result.model_dump() if result is not None else None,
        }
        await self._broadcaster.broadcast(
            WorkspaceEvent(module=DISPATCH_MODULE, type=event_type, payload=payload)
        )
