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
from brain.agents.conscience import Conscience
from brain.agents.deliberation import DEFAULT_TOOL_ACTIONS, Deliberation
from brain.agents.memory_stages import EpisodicLearner, MemoryRecaller
from brain.agents.narrator import Narrator
from brain.agents.sensorium import InputQueue, Sensorium
from brain.cycle import CognitiveCycle
from brain.cycle_control import CycleControlListener
from brain.drives.engine import DriveEngine
from brain.effectors.action_log import ActionAuditReader
from brain.effectors.belt import build_tool_registry
from brain.effectors.dispatch import EffectorDispatch
from brain.effectors.notes import NoteStore
from brain.effectors.scheduler import Scheduler
from brain.effectors.web_consolidator import WebReadConsolidator
from brain.goals.store import GoalStore
from brain.llm.router import LLMRouter, build_router
from brain.memory.consolidator import Consolidator
from brain.memory.decay import MemoryDecay
from brain.memory.episodic import EpisodicMemory
from brain.memory.semantic import SemanticMemory
from brain.memory.snapshot import MemorySnapshot
from brain.memory.working import WorkingMemory
from brain.metacognition.agent import Metacognition
from brain.metacognition.store import MetacognitionStore
from brain.self_model.agent import SelfModel
from brain.self_model.store import IdentityStore
from brain.sleep import SleepCycle, WakeSelfCheck
from brain.workspace import Workspace
from core.audit import AuditWriter
from foundation.config import Settings, get_settings
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
        wake_check: WakeSelfCheck,
        # The read/input surface the /api/v1 endpoints (Phase 5a) consume. The Mind
        # runs headless (FC-8); these are the live instances the HTTP layer reads,
        # wired here in the one composition root (no per-request construction).
        input_queue: InputQueue,
        affect: Affect,
        goals: GoalStore,
        sleep: SleepCycle,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        identity: IdentityStore,
        metacognition: MetacognitionStore,
        action_audit: ActionAuditReader,
        notes: NoteStore,
    ) -> None:
        self.workspace = workspace
        self.registry = registry
        self.router = router
        self.cycle = cycle
        self.drives = drives
        self.self_model = self_model
        self.wake_check = wake_check
        self.input_queue = input_queue
        self.affect = affect
        self.goals = goals
        self.sleep = sleep
        self.episodic = episodic
        self.semantic = semantic
        self.identity = identity
        self.metacognition = metacognition
        self.action_audit = action_audit
        self.notes = notes
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
        # Boot is a wake (SPEC §9.3): verify the persisted self-model + Core invariants
        # are intact BEFORE the heartbeat starts. A boot into a corrupted/tampered
        # self-model comes up degraded (no autonomous action) + alerting, identical to
        # a failed post-sleep check — so a bad row can't be acted on until the next sleep.
        await self.cycle.apply_wake_check(await self.wake_check.verify())
        self._bus_task = asyncio.create_task(self.workspace.run(), name="workspace-bus")
        # The heartbeat auto-starts unless cycle_autostart is off (a frozen
        # maintenance/observation mode — bus + control + API + WS still run, but the
        # loop never ticks, holding a deterministic empty state for the fresh-load
        # smoke + demos). Even when frozen the control channel can step him.
        if get_settings().cycle_autostart:
            self._cycle_task = asyncio.create_task(self.cycle.run(), name="cognitive-cycle")
        else:
            _log.warning("runtime.cycle_autostart_disabled")
        self._control_task = asyncio.create_task(self._control.run(), name="cycle-control")
        _log.info(
            "runtime.started",
            agents=self.registry.names(),
            heartbeat=get_settings().cycle_autostart,
        )

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
    # the pipeline; both reference the one agent instance. The InputQueue is shared
    # with the web API (POST /api/v1/input pushes onto the same Redis list Sensorium
    # drains each tick), so a web message flows through the full cycle (FC-8).
    input_queue = InputQueue()
    sensorium = Sensorium(input_queue=input_queue)
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
    # It shares the one GoalStore the web API reads (GET /api/v1/goals), so the
    # goals panel reflects exactly what Deliberation is pursuing.
    # Deliberation closes the autonomy loop (FC-2/FC-3). In 6b it can select an
    # EXTERNAL tool for a goal (Curiosity→news, Boredom→web_search, Coherence→
    # memory_search, Mastery→code_exec) via DEFAULT_TOOL_ACTIONS; the query/snippet is
    # derived from the goal (the code_exec snippet via an LLM step). Internal actions
    # remain the fallback. Shares the one GoalStore the web API reads (GET /goals).
    goal_store = GoalStore()
    deliberation = Deliberation(router, store=goal_store, tool_actions=DEFAULT_TOOL_ACTIONS)
    registry.register(deliberation)

    # The Scheduler is a between-ticks run-loop phase (FC-7): it fires due
    # self-scheduled wakeups by injecting a self-percept onto the SAME InputQueue the
    # Sensorium drains, so a wakeup flows through the normal perception path. Built
    # before the belt so the schedule_wakeup tool can share this one instance.
    scheduler = Scheduler(input_queue=input_queue)

    # The safe-action substrate (Phase 6a) now carrying 6b's full belt. The Conscience
    # is a registered, prompt-backed inner agent (FC-2/FC-3) that vets a proposed
    # action at CHECK; the EffectorDispatch is the single FC-5 point that runs an
    # allowed tool from the belt and writes the append-only action_log via the Core's
    # import-isolated AuditWriter (FC-1). The belt is built with every 6b tool
    # registered (web/news/fetch/code/note/memory/schedule) — each is automatically
    # vetted + audited just by being on the belt.
    conscience = Conscience(router)
    registry.register(conscience)
    dispatch = EffectorDispatch(
        registry=build_tool_registry(scheduler=scheduler),
        conscience=conscience,
        audit=AuditWriter(),
        broadcaster=workspace,
    )

    # The WebReadConsolidator closes the curiosity loop: after a web-read tool runs,
    # the cycle hands its content here to be summarised into memory (episode + fact
    # with url provenance) — the satisfaction that eases Curiosity fires on THIS, not
    # the fetch (SPEC §8). Uses the same consolidation role/router as sleep (FC-4).
    web_consolidator = WebReadConsolidator(router=router)

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
    # One WakeSelfCheck shared by the sleep pipeline (post-sleep gate) and the runtime
    # (startup gate), over the same self_model + drives, so both reference one truth.
    wake_check = WakeSelfCheck(self_model=self_model, drives=drives)
    sleep_cycle = SleepCycle(
        consolidator=Consolidator(SemanticMemory(), router=router),
        decay=MemoryDecay(),
        self_model=self_model,
        metacognition=Metacognition(router),
        snapshot=MemorySnapshot(working=working_memory),
        drives=drives,
        wake_check=wake_check,
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
        dispatch=dispatch,
        sleep_cycle=sleep_cycle,
        scheduler=scheduler,
        web_consolidator=web_consolidator,
        working_memory=working_memory,
        interval_seconds=settings.cycle_base_interval_seconds,
        min_interval_seconds=settings.cycle_min_interval_seconds,
        max_interval_seconds=settings.cycle_max_interval_seconds,
        arousal_speedup=settings.cycle_arousal_speedup,
        tired_slowdown=settings.cycle_tired_slowdown,
        attention_drive_weight=settings.attention_weight_drive,
        workspace_capacity=settings.workspace_capacity,
    )

    # The web API's read stores (Phase 5a). Stateless read facades over the same
    # tables the cognition path writes — a fresh instance per concern, constructed
    # once here in the composition root (not per HTTP request).
    api_episodic = EpisodicMemory()
    api_semantic = SemanticMemory()
    api_identity = IdentityStore()
    api_metacognition = MetacognitionStore()
    # Read facade over the durable action_log trail (GET /api/v1/audit/actions).
    api_action_audit = ActionAuditReader()
    # Read store for Johnny's journal (GET /api/v1/notes) — newest-first.
    api_notes = NoteStore()

    return CognitiveRuntime(
        workspace=workspace,
        registry=registry,
        router=router,
        cycle=cycle,
        drives=drives,
        self_model=self_model,
        wake_check=wake_check,
        input_queue=input_queue,
        affect=affect,
        goals=goal_store,
        sleep=sleep_cycle,
        episodic=api_episodic,
        semantic=api_semantic,
        identity=api_identity,
        metacognition=api_metacognition,
        action_audit=api_action_audit,
        notes=api_notes,
    )
