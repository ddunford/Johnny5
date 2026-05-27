"""The ``InnerAgent`` protocol and the dynamic ``AgentRegistry``.

Every inner agent conforms to one structural contract so the society is uniform
and runtime-mutable (FC-2):

* ``name`` — unique handle in the registry.
* ``subscribes_to`` — bus event types this agent reacts to (``"*"`` = all). The
  registry wires these onto the workspace; the cycle also drives pipeline agents
  by their domain methods at named stages (FC-7), but the subscription is what
  makes an *event-driven* agent (a reflex, a Phase-9 spawn) work with no cycle
  change.
* ``handle(event) -> [event]`` — react to a bus event, optionally emitting
  follow-up events (which the workspace re-broadcasts).
* ``prompt`` / ``model_route`` — the (git-backed, runtime-editable) prompt text
  and the router role this agent's cognition uses. Externalised config, never a
  code constant (FC-3).

The registry is the single place agents are added/removed. Registering after the
workspace is attached wires the new agent immediately (spawn); unregistering
unwires it (retire) — both without restarting the loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from brain.workspace import Workspace, WorkspaceEvent
from foundation.observability import get_logger

_log = get_logger("brain.agents")


@runtime_checkable
class InnerAgent(Protocol):
    """The contract every inner agent satisfies (see module docstring)."""

    name: str
    subscribes_to: Sequence[str]
    prompt: str
    model_route: str

    async def handle(self, event: WorkspaceEvent) -> Sequence[WorkspaceEvent]:
        """React to a bus event, returning any follow-up events to broadcast."""
        ...


class AgentRegistry:
    """The live set of inner agents, wired to the workspace bus.

    Holds agents by name and, once a workspace is attached, keeps each agent's
    subscriptions wired. Designed for runtime mutation: ``register`` after attach
    spawns an agent into the running society; ``unregister`` retires one.
    """

    def __init__(self) -> None:
        self._agents: dict[str, InnerAgent] = {}
        self._workspace: Workspace | None = None

    def register(self, agent: InnerAgent) -> InnerAgent:
        """Add an agent (wiring its subscriptions if a workspace is attached)."""
        if agent.name in self._agents:
            raise ValueError(f"an agent named {agent.name!r} is already registered")
        self._agents[agent.name] = agent
        if self._workspace is not None:
            self._wire(agent)
        _log.info("agent.registered", agent=agent.name, subscribes_to=list(agent.subscribes_to))
        return agent

    def unregister(self, name: str) -> None:
        """Remove an agent and unwire its subscriptions (no-op if unknown)."""
        agent = self._agents.pop(name, None)
        if agent is None:
            return
        if self._workspace is not None:
            self._unwire(agent)
        _log.info("agent.unregistered", agent=name)

    def attach(self, workspace: Workspace) -> None:
        """Bind the registry to a workspace and wire every current agent's subs."""
        self._workspace = workspace
        for agent in self._agents.values():
            self._wire(agent)

    def get(self, name: str) -> InnerAgent:
        """Return the agent registered under ``name`` (KeyError if absent)."""
        return self._agents[name]

    def all(self) -> list[InnerAgent]:
        """Every registered agent (registration order)."""
        return list(self._agents.values())

    def names(self) -> list[str]:
        """The names of every registered agent."""
        return list(self._agents)

    def __contains__(self, name: object) -> bool:
        return name in self._agents

    def _wire(self, agent: InnerAgent) -> None:
        assert self._workspace is not None
        for event_type in agent.subscribes_to:
            self._workspace.subscribe(event_type, agent.handle)

    def _unwire(self, agent: InnerAgent) -> None:
        assert self._workspace is not None
        for event_type in agent.subscribes_to:
            self._workspace.unsubscribe(event_type, agent.handle)
