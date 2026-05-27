"""The composition root for the running Mind.

The cognitive loop runs headless (FC-8): it is owned here, not by the HTTP layer,
so it ticks whether or not anything is attached. ``CognitiveRuntime`` builds and
owns the long-lived pieces — the Global Workspace, the agent registry, the LLM
router, and the cognitive cycle — and manages their background tasks (the
workspace bus listener and the heartbeat) under the app lifespan.

As the Phase-2 agents land (Sensorium, Attention, memory recall/learn, Narrator)
they are constructed and wired here: registered on the bus *and* injected as the
cycle's stage collaborators. This is the one place the society is assembled, so a
new agent is a single edit here, not a change to the cycle.
"""

from __future__ import annotations

import asyncio
import contextlib

from brain.agents import AgentRegistry
from brain.agents.sensorium import Sensorium
from brain.cycle import CognitiveCycle
from brain.llm.router import LLMRouter, build_router
from brain.memory.working import WorkingMemory
from brain.workspace import Workspace
from foundation.config import Settings
from foundation.observability import get_logger

_log = get_logger("brain.runtime")


class CognitiveRuntime:
    """Owns the workspace, registry, router, and cycle, plus their background tasks."""

    def __init__(
        self,
        *,
        workspace: Workspace,
        registry: AgentRegistry,
        router: LLMRouter,
        cycle: CognitiveCycle,
    ) -> None:
        self.workspace = workspace
        self.registry = registry
        self.router = router
        self.cycle = cycle
        self._bus_task: asyncio.Task[None] | None = None
        self._cycle_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the bus listener and the heartbeat as background tasks."""
        self.registry.attach(self.workspace)
        self._bus_task = asyncio.create_task(self.workspace.run(), name="workspace-bus")
        self._cycle_task = asyncio.create_task(self.cycle.run(), name="cognitive-cycle")
        _log.info("runtime.started", agents=self.registry.names())

    async def stop(self) -> None:
        """Stop the heartbeat and bus listener, then release the router."""
        self.cycle.stop()
        self.workspace.stop()
        for task in (self._cycle_task, self._bus_task):
            if task is None:
                continue
            task.cancel()
            # Shutdown is best-effort: a cancelled or already-failed task must not
            # block teardown of the rest of the runtime.
            with contextlib.suppress(BaseException):
                await task
        await self.router.aclose()
        _log.info("runtime.stopped", ticks=self.cycle.tick_count)


def build_runtime(settings: Settings) -> CognitiveRuntime:
    """Assemble the production runtime from settings.

    Stage collaborators are wired in as their tasks land; until then the cycle
    ticks through its pipeline with the stages it has (the loop shape is fixed,
    FC-7). Working memory (Phase 1) backs the Attention bottleneck.
    """
    workspace = Workspace()
    registry = AgentRegistry()
    router = build_router(settings)
    working_memory = WorkingMemory()

    # Sensorium is both registered (society membership) and injected as the
    # PERCEIVE stage — the registry handles dynamic membership, the cycle drives
    # the pipeline; both reference the one agent instance.
    sensorium = Sensorium()
    registry.register(sensorium)

    cycle = CognitiveCycle(
        workspace,
        perception=sensorium,
        working_memory=working_memory,
        interval_seconds=settings.cycle_base_interval_seconds,
        workspace_capacity=settings.workspace_capacity,
    )

    return CognitiveRuntime(workspace=workspace, registry=registry, router=router, cycle=cycle)
