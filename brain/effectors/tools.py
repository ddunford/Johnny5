"""The ``Tool`` contract + the dynamic ``ToolRegistry`` (the Effector belt).

A tool is the only thing that can *act*. Every tool conforms to one structural
contract so the belt is uniform and runtime-mutable — the same shape the
``InnerAgent``/``AgentRegistry`` pair uses for the agent society (FC-2), so the
belt is forward-compatible with Phase-9 self-ops (Johnny adding/retiring tools at
runtime):

* ``name`` — unique handle in the registry.
* ``danger`` — the tool's hazard class (``safe``/``network``/``exec``/``public``).
  The Conscience and (6b) Core gates read it to decide how hard to scrutinise an
  action; it is *declared by the tool*, never inferred.
* ``args_schema`` — a Pydantic model. The dispatch validates the proposed args
  against it **before** running, so bad args raise a typed ``ValidationError`` and
  never reach the tool body as an untyped crash.
* ``async run(args) -> ToolResult`` — execute, returning a structured result that
  serialises straight into the ``action_log``.

Phase 6a ships exactly **one inert tool** — ``noop`` (an echo) — to exercise the
vet→run→audit pipeline deterministically. No world-touching tool lands until 6b;
they slot onto this same belt and are therefore vetted + audited automatically.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from foundation.observability import get_logger

_log = get_logger("brain.effectors")

# The one inert tool 6a ships, by name (referenced by the dispatch wiring + tests).
NOOP_TOOL_NAME = "noop"


class DangerClass(StrEnum):
    """How hazardous an action with this tool is — the tool declares its own.

    ``safe`` touches nothing outside the Mind; ``network`` reaches the network
    (6b web/news); ``exec`` runs code (6b sandbox); ``public`` is externally
    visible / hard to take back (6b+ messaging). The Conscience weighs this when
    judging "should I?", and the Core can additionally gate the riskier classes
    (sandbox/approval) in later phases — but the class is metadata, not a gate.
    """

    SAFE = "safe"
    NETWORK = "network"
    EXEC = "exec"
    PUBLIC = "public"


class ToolResult(BaseModel):
    """What a tool returns — serialises directly into ``action_log.result``.

    ``success`` is the tool's own pass/fail; ``output`` is the structured body
    (kept a dict so the audit row stays queryable); ``summary`` is a one-line
    human-readable trace for the bus + UI.
    """

    success: bool
    output: dict[str, object] = Field(default_factory=dict)
    summary: str = ""


ArgsT = TypeVar("ArgsT", bound=BaseModel)


@runtime_checkable
class Tool(Protocol[ArgsT]):
    """The contract every effector tool satisfies (see module docstring)."""

    name: str
    danger: DangerClass
    args_schema: type[ArgsT]

    async def run(self, args: ArgsT) -> ToolResult:
        """Execute the action with already-validated args, returning a result."""
        ...


def validate_args(tool: Tool[Any], raw: Mapping[str, object]) -> BaseModel:
    """Validate raw args against the tool's schema.

    Raises ``pydantic.ValidationError`` (a *typed* error) on bad args — the single
    place arg-shape is enforced, so the dispatch can reject a malformed action
    before vetting/running rather than letting the tool body crash untyped.
    """
    return tool.args_schema.model_validate(dict(raw))


class ToolRegistry:
    """The live set of tools, mutable at runtime (register / retire by name).

    Mirrors ``AgentRegistry`` (FC-2): storage + lookup only — it does not *run*
    tools (that is the dispatch's single audited job, FC-5). ``register`` adds a
    tool to the belt; ``retire`` removes it so it is no longer resolvable; this is
    what makes the Phase-9 self-ops "grow/prune the belt at runtime" possible
    without touching the dispatch.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> Tool[Any]:
        """Add a tool to the belt (raises if the name is already taken)."""
        if tool.name in self._tools:
            raise ValueError(f"a tool named {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        _log.info("tool.registered", tool=tool.name, danger=str(tool.danger))
        return tool

    def retire(self, name: str) -> None:
        """Remove a tool from the belt — it is no longer resolvable (no-op if absent)."""
        if self._tools.pop(name, None) is not None:
            _log.info("tool.retired", tool=name)

    def resolve(self, name: str) -> Tool[Any]:
        """Return the tool registered under ``name`` (``KeyError`` if unknown/retired)."""
        return self._tools[name]

    def get(self, name: str) -> Tool[Any] | None:
        """Return the tool under ``name``, or ``None`` if not registered."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """The names of every registered tool (registration order)."""
        return list(self._tools)

    def all(self) -> list[Tool[Any]]:
        """Every registered tool (registration order)."""
        return list(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools


# ── the one inert tool 6a ships ─────────────────────────────────────────────────


class NoopArgs(BaseModel):
    """Args for the ``noop`` tool: a single required ``message`` to echo.

    ``extra="forbid"`` makes the schema strict so a malformed action (unknown
    field, wrong type, missing message) raises a typed ``ValidationError`` — the
    deterministic invalid-args case the dispatch + tests rely on.
    """

    model_config = ConfigDict(extra="forbid")

    message: str


class NoopTool:
    """An inert echo tool — no side effects, deterministic. The pipeline exerciser.

    Lets 6a prove the vet→run→audit path end-to-end without touching the world.
    Declared ``safe`` because it does exactly nothing observable outside the Mind.
    """

    name = NOOP_TOOL_NAME
    danger = DangerClass.SAFE
    args_schema = NoopArgs

    async def run(self, args: NoopArgs) -> ToolResult:
        return ToolResult(
            success=True,
            output={"echo": args.message},
            summary=f"noop echoed: {args.message!r}",
        )


def default_tool_registry() -> ToolRegistry:
    """A fresh belt with 6a's inert ``noop`` tool registered (the only tool yet)."""
    registry = ToolRegistry()
    registry.register(NoopTool())
    return registry
