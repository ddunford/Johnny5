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

from brain.affect.agent import Affect
from brain.agents import AgentRegistry
from brain.agents.attention import Attention
from brain.agents.deliberation import Deliberation
from brain.agents.memory_stages import EpisodicLearner, MemoryRecaller
from brain.agents.narrator import Narrator
from brain.agents.sensorium import Sensorium
from brain.cycle import CognitiveCycle
from brain.cycle_control import CycleControlListener
from brain.drives.engine import DriveEngine
from brain.llm.router import LLMRouter, build_router
from brain.memory.consolidator import Consolidator
from brain.memory.decay import MemoryDecay
from brain.memory.semantic import SemanticMemory
from brain.memory.snapshot import MemorySnapshot
from brain.memory.working import WorkingMemory
from brain.metacognition.agent import Metacognition
from brain.self_model.agent import SelfModel
from brain.sleep import SleepCycle
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
        drives: DriveEngine,
        self_model: SelfModel,
    ) -> None:
        self.workspace = workspace
        self.registry = registry
        self.router = router
        self.cycle = cycle
        self.drives = drives
        self.self_model = self_model
        self._control = CycleControlListener(cycle)
        self._bus_task: asyncio.Task[None] | None = None
        self._cycle_task: asyncio.Task[None] | None = None
        self._control_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the bus listener, heartbeat, and REPL control listener.

        Drives are bootstrapped first — rows ensured + parameters re-synced from
        ``drives.toml`` (FC-3) — without resetting the persisted pressure, so the
        homeostatic state carries across the restart (FC-6). The self-model is
        bootstrapped with the SAME idempotent helper a test fixture uses, so a fresh
        DB (or one whose ``identity`` was wiped) self-heals to the anchor-grounded v1
        baseline rather than waiting for the first sleep.
        """
        self.registry.attach(self.workspace)
        await self.drives.bootstrap()
        await self.self_model.bootstrap()
        self._bus_task = asyncio.create_task(self.workspace.run(), name="workspace-bus")
        self._cycle_task = asyncio.create_task(self.cycle.run(), name="cognitive-cycle")
        self._control_task = asyncio.create_task(self._control.run(), name="cycle-control")
        _log.info("runtime.started", agents=self.registry.names())

    async def stop(self) -> None:
        """Stop the heartbeat, bus, and control listeners, then release the router."""
        self.cycle.stop()
        self.workspace.stop()
        self._control.stop()
        for task in (self._cycle_task, self._bus_task, self._control_task):
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
    attention = Attention()
    registry.register(sensorium)
    registry.register(attention)

    # The Narrator thinks via the router (FC-4); it's a registered, prompt-backed
    # inner agent (Johnny can edit its voice, FC-3).
    narrator = Narrator(router)
    registry.register(narrator)

    # Affect appraises the tick into a persisted mood (registered inner agent,
    # FC-2/FC-3); the Drive engine is the homeostatic core (a stage collaborator,
    # no prompt of its own). Both fill the Phase-2 APPRAISE stub (FC-7).
    affect = Affect(router)
    registry.register(affect)
    drives = DriveEngine()

    # Deliberation closes the autonomy loop (registered inner agent, FC-2/FC-3): it
    # arbitrates urges → a goal and acts on it with an INTERNAL action only
    # (reflect/recall/consolidate/formulate-question) — external tools are Phase 6.
    # It owns the goal store + arbiter so the cycle's DELIBERATE/ACT stay thin.
    deliberation = Deliberation(router)
    registry.register(deliberation)

    # Memory recall/learn are stage collaborators, not bus agents (no prompt to
    # edit) — they bridge the cycle to the Phase-1 memory spine.
    recaller = MemoryRecaller()
    learner = EpisodicLearner()

    # Sleep — the offline growth phase the run loop enters between ticks (FC-7). It
    # shares the one DriveEngine so restore-energy/persistence land on live state,
    # and the cloud-first consolidation/self_model/metacognition roles go through the
    # same router (FC-4). Bounded: the Consolidator caps LLM calls per sleep. The one
    # SelfModel instance is shared with the runtime so startup bootstraps the same
    # one sleep refreshes (prod + test seed identically via SelfModel.bootstrap()).
    self_model = SelfModel(router)
    sleep_cycle = SleepCycle(
        consolidator=Consolidator(SemanticMemory(), router=router),
        decay=MemoryDecay(),
        self_model=self_model,
        metacognition=Metacognition(router),
        snapshot=MemorySnapshot(working=working_memory),
        drives=drives,
    )

    cycle = CognitiveCycle(
        workspace,
        perception=sensorium,
        attention=attention,
        recall=recaller,
        narration=narrator,
        learning=learner,
        drives=drives,
        affect=affect,
        deliberation=deliberation,
        sleep_cycle=sleep_cycle,
        working_memory=working_memory,
        interval_seconds=settings.cycle_base_interval_seconds,
        min_interval_seconds=settings.cycle_min_interval_seconds,
        max_interval_seconds=settings.cycle_max_interval_seconds,
        arousal_speedup=settings.cycle_arousal_speedup,
        tired_slowdown=settings.cycle_tired_slowdown,
        attention_drive_weight=settings.attention_weight_drive,
        workspace_capacity=settings.workspace_capacity,
    )

    return CognitiveRuntime(
        workspace=workspace,
        registry=registry,
        router=router,
        cycle=cycle,
        drives=drives,
        self_model=self_model,
    )
