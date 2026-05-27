"""The cognitive cycle — Johnny's heartbeat (``SPEC §7``).

A continuous async loop that orchestrates one fixed pipeline per tick:

    perceive → appraise* → attend → recall → narrate → deliberate* → check* → act* → learn

The shape is fixed (FC-7): the starred stages are explicit stubs at known
positions that Phase 3 (Affect/Drives) and Phase 6 (Conscience/Effectors) fill in
*in place* — they are called every tick and currently no-op, so later phases slot
in without restructuring the loop. The real Phase-2 stages delegate to injected
collaborators (Sensorium, Attention, memory recall/learn, Narrator); each is
optional so the skeleton ticks on its own and tests can inject mocks.

Three invariants the loop must never violate:

* **It never dies.** Every stage is wrapped — a failed stage (or a "tired" LLM
  provider) is logged, recorded on the tick, and the heartbeat continues
  (graceful degradation, ``SPEC §7``).
* **It never busy-spins.** The run loop sleeps the configured interval between
  ticks (injectable, so tests don't wait on the wall clock) and backs off after
  an unexpected error.
* **It is steppable.** ``tick()`` is one deterministic pass with no sleeping — the
  unit the frozen-clock harness drives. ``run()`` adds the rhythm and the
  pause/step/resume gate the REPL controls.

Agents never call each other here; the cycle moves data between them through the
Global Workspace, and every stage output is broadcast on the bus for
observability (FC-8).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from brain.affect.appraisal import Mood
from brain.agents.attention import AttentionBias
from brain.agents.conscience import ProposedAction
from brain.agents.deliberation import Action, ActionOutcome, DeliberationResult
from brain.drives.engine import EVENT_INTERACTION, SLEEP_DRIVE, DriveEvent, DriveReading, Urge
from brain.effectors.dispatch import EffectorDispatch, VettedAction
from brain.effectors.scheduler import Scheduler
from brain.goals.store import Goal, goals_to_payload
from brain.memory.working import WorkingMemory, WorkingMemoryItem
from brain.sleep import CheckResult, SleepCycle, SleepLog, SleepReport
from brain.workspace import Workspace, WorkspaceEvent, WorkspaceItem
from foundation.observability import get_logger

_log = get_logger("brain.cycle")

# Minimum backoff after an unexpected tick error, so a persistent failure can't
# turn the loop into a hot spin even if the configured interval is tiny.
_ERROR_BACKOFF_SECONDS = 1.0

# The percept kind a fresh interaction arrives as (a high-salience input).
_INPUT_KIND = "input"
# The consolidated state-surface event the ``/ws/state`` channel + REPL read (FC-8):
# one snapshot per tick of drives + mood + the active goal.
STATE_EVENT = "state"
# Drive → the percept kinds attending to it helps satisfy (the FC-7 bias slot):
# an unmet Connection pulls toward fresh input; unmet Curiosity toward recall.
_DRIVE_KIND_RELEVANCE: dict[str, tuple[str, ...]] = {
    "connection": ("input",),
    "curiosity": ("memory", "fact"),
    "boredom": ("input", "memory"),
}


# ── stage collaborators (each optional; the cycle no-ops when one is absent) ───


@runtime_checkable
class PerceptionStage(Protocol):
    """Sensorium: turn pending inputs into salient percepts (``SPEC §5`` #1)."""

    async def perceive(self) -> Sequence[WorkspaceItem]: ...


@runtime_checkable
class AttentionStage(Protocol):
    """Attention: the bottleneck — select a bounded salient set (``SPEC §5`` #2)."""

    async def select(
        self,
        *,
        working_memory: Sequence[WorkspaceItem],
        percepts: Sequence[WorkspaceItem],
    ) -> Sequence[WorkspaceItem]: ...


@runtime_checkable
class RecallStage(Protocol):
    """Memory: pull relevant episodes/facts into the workspace (``SPEC §5`` #4/5)."""

    async def recall(self, *, focus: Sequence[WorkspaceItem]) -> Sequence[WorkspaceItem]: ...


@runtime_checkable
class NarrationStage(Protocol):
    """Inner Narrator: emit the first-person thought for the tick (``SPEC §5`` #10).

    Returns the thought text, or ``None`` when it can't narrate this tick (e.g.
    every provider is tired) — the cycle degrades by skipping the broadcast.
    """

    async def narrate(self, *, contents: Sequence[WorkspaceItem]) -> str | None: ...


@runtime_checkable
class LearningStage(Protocol):
    """Episodic write of a tick worth remembering (``SPEC §7`` step 9)."""

    async def learn(self, *, contents: Sequence[WorkspaceItem], thought: str | None) -> None: ...


@runtime_checkable
class DriveStage(Protocol):
    """Drives: advance the homeostatic pressures and emit urges (``SPEC §6.1``)."""

    async def step(
        self, events: Sequence[DriveEvent] = (), *, now: datetime | None = None
    ) -> Sequence[DriveReading]: ...

    def urges(self, readings: Sequence[DriveReading]) -> Sequence[Urge]: ...


@runtime_checkable
class AffectStage(Protocol):
    """Affect: appraise the tick into the running mood (``SPEC §6.2``)."""

    async def appraise_tick(
        self,
        *,
        contents: Sequence[WorkspaceItem],
        drives: Sequence[DriveReading],
        events: Sequence[DriveEvent] = (),
        now: datetime | None = None,
    ) -> Mood: ...


@runtime_checkable
class DeliberationStage(Protocol):
    """Deliberation: arbitrate to a goal and plan/execute an internal action."""

    async def deliberate(
        self,
        *,
        urges: Sequence[Urge],
        mood: Mood | None,
        contents: Sequence[WorkspaceItem],
        now: datetime | None = None,
    ) -> DeliberationResult: ...

    async def act(
        self, action: Action, goal: Goal, contents: Sequence[WorkspaceItem]
    ) -> ActionOutcome: ...


# ── per-tick state + report ────────────────────────────────────────────────────


@dataclass
class CycleContext:
    """Mutable scratchpad threaded through one tick's stages."""

    tick: int
    percepts: list[WorkspaceItem] = field(default_factory=list)
    contents: list[WorkspaceItem] = field(default_factory=list)
    thought: str | None = None
    drives: list[DriveReading] = field(default_factory=list)
    mood: Mood | None = None
    urges: list[Urge] = field(default_factory=list)
    events: list[DriveEvent] = field(default_factory=list)
    goal: Goal | None = None
    action: Action | None = None
    # A tool action resolved + vetted at CHECK, awaiting execution at ACT (FC-7).
    vetted: VettedAction | None = None
    stage_errors: dict[str, str] = field(default_factory=dict)


@dataclass
class TickReport:
    """A summary of one tick (for tests, the REPL, and structured logging)."""

    tick: int
    percept_count: int
    content_count: int
    thought: str | None
    stage_errors: dict[str, str]

    @property
    def ok(self) -> bool:
        """True when no stage degraded this tick."""
        return not self.stage_errors


# ── the pause/step/resume gate (drives the REPL's cockpit controls) ─────────────


class CycleGate:
    """An async gate the run loop checks before each tick.

    Running → ticks proceed freely. Paused → the loop blocks until ``resume`` or
    a single ``step``. Built lazily on first use so it binds to the running loop.
    """

    def __init__(self) -> None:
        self._paused = False
        self._step_once = False
        self._proceed: asyncio.Event | None = None

    def _event(self) -> asyncio.Event:
        if self._proceed is None:
            self._proceed = asyncio.Event()
            self._proceed.set()
        return self._proceed

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._step_once = False
        self._event().clear()

    def resume(self) -> None:
        self._paused = False
        self._step_once = False
        self._event().set()

    def step(self) -> None:
        """Allow exactly one tick, then stay paused."""
        self._paused = True
        self._step_once = True
        self._event().set()

    async def wait_turn(self) -> None:
        await self._event().wait()
        if self._paused and self._step_once:
            # Consume the single step; re-block before the next tick.
            self._step_once = False
            self._event().clear()


# ── the cycle ────────────────────────────────────────────────────────────────


class CognitiveCycle:
    """Orchestrates the tick pipeline and owns the heartbeat loop."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        perception: PerceptionStage | None = None,
        attention: AttentionStage | None = None,
        recall: RecallStage | None = None,
        narration: NarrationStage | None = None,
        learning: LearningStage | None = None,
        drives: DriveStage | None = None,
        affect: AffectStage | None = None,
        deliberation: DeliberationStage | None = None,
        dispatch: EffectorDispatch | None = None,
        sleep_cycle: SleepCycle | None = None,
        scheduler: Scheduler | None = None,
        working_memory: WorkingMemory | None = None,
        interval_seconds: float = 4.0,
        min_interval_seconds: float = 1.5,
        max_interval_seconds: float = 12.0,
        arousal_speedup: float = 1.0,
        tired_slowdown: float = 1.5,
        attention_drive_weight: float = 0.5,
        workspace_capacity: int = 7,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._workspace = workspace
        self._perception = perception
        self._attention = attention
        self._recall = recall
        self._narration = narration
        self._learning = learning
        self._drives = drives
        self._affect = affect
        self._deliberation = deliberation
        self._dispatch = dispatch
        self._sleep_cycle = sleep_cycle
        self._scheduler = scheduler
        self._working_memory = working_memory
        self._interval = interval_seconds
        # Bounds on the affect-modulated interval (the floor is the 3.12 spin guard).
        self._min_interval = min_interval_seconds
        self._max_interval = max_interval_seconds
        self._arousal_speedup = arousal_speedup
        self._tired_slowdown = tired_slowdown
        self._attention_drive_weight = attention_drive_weight
        self._capacity = workspace_capacity
        self._sleep = sleep_fn
        self._gate = CycleGate()
        self._running = False
        self._tick = 0
        # The next sleep, set by APPRAISE from mood/energy; the base until affect runs.
        self._next_interval = interval_seconds
        # Outcome events Deliberation/Act queue this tick, consumed by the *next*
        # APPRAISE so a goal's result feeds drives+affect (FC-5 dispatch → feedback).
        self._pending_events: list[DriveEvent] = []
        # Sleep is a run-loop phase between ticks (FC-7), not a tick stage. The last
        # tick's urges feed the trigger; degraded ticks since the last sleep feed the
        # metacognition review; the last report surfaces on /ws/state + the REPL.
        self._last_urges: list[Urge] = []
        self._degraded_ticks = 0
        self._last_sleep: SleepReport | None = None
        # Full agency gate (SPEC §9.3): a failed wake self-check after sleep drops
        # this to False, suppressing DELIBERATE + ACT until a later check passes.
        self._full_agency = True

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def tick_count(self) -> int:
        return self._tick

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._gate.paused

    @property
    def has_full_agency(self) -> bool:
        """False while in post-sleep degraded mode (autonomous action suspended)."""
        return self._full_agency

    def pause(self) -> None:
        self._gate.pause()

    def resume(self) -> None:
        self._gate.resume()

    def step(self) -> None:
        self._gate.step()

    def stop(self) -> None:
        """Ask the run loop to exit; unblocks the gate if paused."""
        self._running = False
        self._gate.resume()

    def enqueue_drive_event(self, event: DriveEvent) -> None:
        """Queue an outcome event (success/learning/…) for the next APPRAISE.

        Deliberation/Act report a goal's result here (FC-5 dispatch → feedback);
        the *next* tick's drive step consumes it, so an achieved goal eases the
        drive that spawned it and colours mood. Applied next tick (not this one) to
        keep each drive step a single elapsed-time advance.
        """
        self._pending_events.append(event)

    @property
    def next_interval(self) -> float:
        """The interval the heartbeat will sleep after the last tick (for the REPL)."""
        return self._next_interval

    async def run(self) -> None:
        """The heartbeat: tick, sleep, repeat — until ``stop`` is called.

        Wrapped so neither a stage failure (already handled per-stage) nor an
        unexpected error escapes and kills the loop; the latter triggers a short
        backoff before retrying so a persistent fault can't hot-spin.
        """
        self._running = True
        _log.info("cycle.run.start", interval=self._interval)
        try:
            while self._running:
                await self._gate.wait_turn()
                if not self._running:
                    break
                try:
                    await self.tick()
                    # Between-ticks run-loop phases (FC-7), not tick stages. Fire any due
                    # self-scheduled wakeups first (they enqueue a self-percept the next
                    # tick perceives), then enter the offline sleep phase if its trigger
                    # fires. Both run while normal ticking is paused; tick() is untouched.
                    await self._fire_due_wakeups()
                    # Sleep is a phase the loop enters BETWEEN ticks (FC-7): when the
                    # trigger fires, normal ticking pauses while the offline pipeline
                    # runs, then the heartbeat resumes. tick()'s shape is untouched.
                    await self._maybe_sleep()
                    # APPRAISE set the next interval from mood/energy (bounded); the
                    # heartbeat speeds up when aroused and drags when tired.
                    await self._sleep(self._next_interval)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _log.exception("cycle.tick.unexpected_error", tick=self._tick)
                    await self._sleep(max(self._next_interval, _ERROR_BACKOFF_SECONDS))
        finally:
            self._running = False
            _log.info("cycle.run.stop", ticks=self._tick)

    async def _fire_due_wakeups(self) -> None:
        """Between ticks, fire any due self-scheduled wakeups (FC-7 run-loop phase).

        Each due wakeup is claimed (atomically flipped ``pending → fired`` so it
        can't double-fire) and injected as a self-percept onto the Sensorium's input
        queue, surfacing on the next tick's PERCEIVE — Johnny "remembers" his earlier
        intention. A scheduler failure degrades this phase but never the heartbeat.
        """
        if self._scheduler is None:
            return
        try:
            fired = await self._scheduler.fire_due()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a due-check failure must not kill the loop
            _log.warning("cycle.wakeups.degraded", tick=self._tick, error=str(exc))
            return
        if fired:
            _log.info("cycle.wakeups.fired", count=len(fired), tick=self._tick)

    async def _maybe_sleep(self) -> None:
        """Between ticks, enter the offline sleep phase if the trigger fires (FC-7).

        Checks the sleep trigger against the last tick's urges (Energy over threshold)
        or the cadence; when due, broadcasts the awake→asleep transition, runs the
        bounded sleep pipeline (normal ticking is paused — we're between ticks), then
        broadcasts asleep→awake with the summary. A wedged sleep is impossible: the
        pipeline is per-stage isolated and always returns. ``tick()`` is never touched.
        """
        if self._sleep_cycle is None:
            return
        trigger = self._sleep_cycle.sleep_trigger(self._last_urges, tick=self._tick)
        if trigger is None:
            return
        _log.info("cycle.sleep.enter", trigger=trigger, tick=self._tick)
        await self._emit_sleep_transition(asleep=True)
        report = await self._sleep_cycle.sleep(trigger=trigger, degraded_ticks=self._degraded_ticks)
        if report is not None:
            self._last_sleep = report
            self._degraded_ticks = 0
            # GATE full-agency resume on the wake self-check (SPEC §9.3): a failed
            # check (corrupted/blanked self-model, or a name that diverged from the
            # immutable anchor) drops Johnny into degraded mode — he keeps perceiving/
            # appraising/narrating (the heartbeat lives + stays observable) but takes
            # NO autonomous action until a later wake self-check passes (e.g. after a
            # restore-from-backup). A passing check restores full agency.
            self._full_agency = report.self_check_ok
            if not report.self_check_ok:
                await self._alert_degraded(report)
        await self._emit_sleep_transition(asleep=False)
        _log.info(
            "cycle.sleep.wake",
            self_check_ok=report.self_check_ok if report else None,
            self_model_version=report.self_model_version if report else None,
            full_agency=self._full_agency,
        )

    async def apply_wake_check(self, result: CheckResult) -> None:
        """Set the full-agency gate from a wake self-check verdict (used at startup).

        SPEC §9.3 treats resuming from persisted state as a wake: boot is exactly
        when a corrupted/tampered ``identity`` row gets loaded, so the runtime runs
        the wake self-check before the heartbeat starts and applies it here. A boot
        into a bad self-model comes up degraded + alerting, identical to a failed
        sleep — closing the window where Johnny would act on it until the next sleep.
        """
        self._full_agency = result.ok
        if not result.ok:
            await self._emit_degraded_alert([f.check for f in result.failures], source="startup")

    async def _alert_degraded(self, report: SleepReport) -> None:
        """Flag that a failed *post-sleep* wake self-check suspended autonomous action."""
        failures = report.notes.get("self_check_failures", [])
        await self._emit_degraded_alert(
            failures if isinstance(failures, list) else [], source="sleep"
        )

    async def _emit_degraded_alert(self, failures: list[str], *, source: str) -> None:
        """Warning log + ``sleep.degraded`` bus event (surfaced on /ws/state + REPL)."""
        _log.warning(
            "cycle.agency.degraded",
            reason=f"{source}_wake_self_check_failed",
            failures=failures,
        )
        await self._workspace.broadcast(
            WorkspaceEvent(
                module="sleep",
                type="sleep.degraded",
                payload={
                    "reason": f"wake self-check failed ({source}) — autonomous action suspended",
                    "self_check_failures": failures,
                    "full_agency": False,
                },
            )
        )

    async def _emit_sleep_transition(self, *, asleep: bool) -> None:
        """Broadcast a state snapshot marking the awake↔asleep transition (FC-8).

        Emitted outside a tick (there is no ``ctx`` mid-sleep), so the cognition
        fields are empty and only the ``sleep`` block is meaningful — enough for a
        consumer to flip its awake/asleep indicator and show the last-sleep summary.
        """
        await self._workspace.broadcast(
            WorkspaceEvent(
                module="sleep",
                type=STATE_EVENT,
                payload=serialize_state(
                    tick=self._tick,
                    drives=[],
                    mood=None,
                    goals=[],
                    interval=self._next_interval,
                    sleep=self._sleep_block(asleep=asleep),
                ),
            )
        )

    # ── one tick ───────────────────────────────────────────────────────────────

    async def tick(self) -> TickReport:
        """Run one full pipeline pass and return a summary. Never raises on a
        stage failure — each stage is isolated so the heartbeat survives."""
        self._tick += 1
        ctx = CycleContext(tick=self._tick)
        await self._emit(ctx, "cycle", "cycle.tick", {"tick": self._tick}, stage=None)

        await self._stage(ctx, "perceive", self._perceive)
        await self._stage(ctx, "appraise", self._appraise)
        await self._stage(ctx, "attend", self._attend)
        await self._stage(ctx, "recall", self._recall_stage)
        await self._stage(ctx, "narrate", self._narrate)
        await self._stage(ctx, "deliberate", self._deliberate)
        await self._stage(ctx, "check", self._check)
        await self._stage(ctx, "act", self._act)
        await self._stage(ctx, "learn", self._learn)
        await self._stage(ctx, "state", self._broadcast_state)

        if ctx.stage_errors:
            # Count degraded ticks since the last sleep — the metacognition review
            # window reads this ("my thinking degraded N times").
            self._degraded_ticks += 1
        report = TickReport(
            tick=ctx.tick,
            percept_count=len(ctx.percepts),
            content_count=len(ctx.contents),
            thought=ctx.thought,
            stage_errors=dict(ctx.stage_errors),
        )
        _log.debug(
            "cycle.tick.done",
            tick=report.tick,
            percepts=report.percept_count,
            contents=report.content_count,
            degraded=list(report.stage_errors),
        )
        return report

    async def _stage(
        self, ctx: CycleContext, name: str, fn: Callable[[CycleContext], Awaitable[None]]
    ) -> None:
        """Run one stage; a failure degrades that stage but never the tick."""
        try:
            await fn(ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # graceful degradation — the heartbeat goes on
            ctx.stage_errors[name] = f"{type(exc).__name__}: {exc}"
            _log.warning("cycle.stage.degraded", tick=ctx.tick, stage=name, error=str(exc))

    # ── stages: real this phase ──────────────────────────────────────────────

    async def _perceive(self, ctx: CycleContext) -> None:
        """PERCEIVE — Sensorium normalises pending inputs into percepts."""
        if self._perception is None:
            return
        ctx.percepts = list(await self._perception.perceive())
        for percept in ctx.percepts:
            await self._emit(
                ctx,
                "sensorium",
                "percept",
                {"kind": percept.kind, "content": percept.content, "source": percept.source},
                stage="perceive",
            )

    async def _attend(self, ctx: CycleContext) -> None:
        """ATTEND — the bottleneck: select a bounded salient set into the workspace.

        New percepts are folded into working memory (so they persist across ticks
        and decay), then Attention selects from working memory + percepts. The
        cycle ages working memory each tick — that decay is a heartbeat concern.
        """
        working_items = await self._refresh_working_memory(ctx.percepts)

        if self._attention is not None:
            selected = await self._attention.select(
                working_memory=working_items, percepts=ctx.percepts
            )
        else:
            # No Attention wired yet: pass percepts through.
            selected = list(ctx.percepts)
        # Defensive bound — the bottleneck invariant ("the workspace never grows
        # unbounded") holds even if a misbehaving Attention over-selects.
        ctx.contents = list(selected)[: self._capacity]

        await self._workspace.set_contents(ctx.contents)
        await self._emit(
            ctx,
            "attention",
            "attention.selected",
            {"count": len(ctx.contents), "items": [i.content for i in ctx.contents]},
            stage="attend",
        )

    async def _refresh_working_memory(
        self, percepts: Sequence[WorkspaceItem]
    ) -> list[WorkspaceItem]:
        """Fold percepts into working memory, decay it, return its live contents."""
        if self._working_memory is None:
            return list(percepts)
        for percept in percepts:
            await self._working_memory.put(
                WorkingMemoryItem(
                    content=percept.content, kind=percept.kind, salience=percept.salience
                )
            )
        await self._working_memory.decay()
        return [
            WorkspaceItem(kind=item.kind, content=item.content, salience=item.salience)
            for item in await self._working_memory.contents()
        ]

    async def _recall_stage(self, ctx: CycleContext) -> None:
        """RECALL — Memory injects relevant episodes/facts alongside the percepts."""
        if self._recall is None:
            return
        recalled = list(await self._recall.recall(focus=ctx.contents))
        if not recalled:
            return
        # Recalled items join the workspace, kept within the bound (salient-first).
        merged = sorted([*ctx.contents, *recalled], key=lambda i: i.salience, reverse=True)
        ctx.contents = merged[: self._capacity]
        await self._workspace.set_contents(ctx.contents)
        await self._emit(ctx, "memory", "recall", {"recalled": len(recalled)}, stage="recall")

    async def _narrate(self, ctx: CycleContext) -> None:
        """NARRATE — the Inner Narrator emits the first-person thought for the tick."""
        if self._narration is None:
            return
        ctx.thought = await self._narration.narrate(contents=ctx.contents)
        if ctx.thought:
            await self._emit(ctx, "narrator", "thought", {"text": ctx.thought}, stage="narrate")

    async def _learn(self, ctx: CycleContext) -> None:
        """LEARN — episodic write of a tick worth remembering."""
        if self._learning is None:
            return
        await self._learning.learn(contents=ctx.contents, thought=ctx.thought)

    # ── stages: explicit stubs (filled in place by Phase 3 / Phase 6) ──────────

    async def _appraise(self, ctx: CycleContext) -> None:
        """APPRAISE — Drives + Affect update from percepts, decay, and outcomes.

        Fills the Phase-2 stub in place (FC-7). Drives advance first (so Affect
        sees the fresh pressures and any satisfaction), then Affect appraises the
        situation into the running mood. Both are broadcast (FC-8 — ``/ws/state``
        consumes them) and fed forward as this tick's attention/recall/narration
        bias and the next heartbeat interval. Each is optional: with neither wired,
        APPRAISE is a no-op and the loop ticks at its base rate, as before.
        """
        ctx.events = self._collect_events(ctx)

        if self._drives is not None:
            ctx.drives = list(await self._drives.step(ctx.events))
            ctx.urges = list(self._drives.urges(ctx.drives))
            # Remember this tick's urges for the between-ticks sleep trigger (FC-7).
            self._last_urges = ctx.urges
            await self._broadcast_drives(ctx)

        if self._affect is not None:
            ctx.mood = await self._affect.appraise_tick(
                contents=ctx.percepts, drives=ctx.drives, events=ctx.events
            )
            await self._emit(ctx, "affect", "mood", serialize_mood(ctx.mood), stage="appraise")

        self._apply_affective_context(ctx)
        self._next_interval = self._modulate_interval(ctx.mood, ctx.drives)

    def _collect_events(self, ctx: CycleContext) -> list[DriveEvent]:
        """This tick's drive events: queued outcomes + an interaction per fresh input."""
        events = list(self._pending_events)
        self._pending_events.clear()
        inputs = sum(1 for p in ctx.percepts if p.kind == _INPUT_KIND)
        events.extend(DriveEvent(kind=EVENT_INTERACTION) for _ in range(inputs))
        return events

    async def _broadcast_drives(self, ctx: CycleContext) -> None:
        """Broadcast the drive levels + any urges (FC-8: the state surface reads this)."""
        await self._emit(
            ctx,
            "drives",
            "drive.state",
            {
                "drives": {d.drive: round(d.value, 4) for d in ctx.drives},
                "over_threshold": [u.drive for u in ctx.urges],
            },
            stage="appraise",
        )
        for urge in ctx.urges:
            event_type = "drive.sleep_signal" if urge.is_sleep_signal else "drive.urge"
            await self._emit(
                ctx,
                "drives",
                event_type,
                {
                    "drive": urge.drive,
                    "urgency": round(urge.urgency, 4),
                    "value": round(urge.value, 4),
                },
                stage="appraise",
            )

    async def _broadcast_state(self, ctx: CycleContext) -> None:
        """Emit the consolidated state snapshot for this tick (FC-8 state surface).

        One stable-schema event per tick carrying drive levels, current mood, and
        the active goal — what ``/ws/state`` and the REPL drive-bar view consume.
        Emitted even with no drives/affect wired (empty fields), so the surface is
        always present for a consumer to attach to.
        """
        await self._emit(
            ctx,
            "cycle",
            STATE_EVENT,
            serialize_state(
                tick=ctx.tick,
                drives=ctx.drives,
                mood=ctx.mood,
                goals=[ctx.goal] if ctx.goal is not None else [],
                interval=self._next_interval,
                sleep=self._sleep_block(asleep=False),
            ),
            stage="state",
        )

    def _sleep_block(self, *, asleep: bool) -> dict[str, object]:
        """The sleep status carried on every state snapshot (FC-8 consumers read this).

        ``asleep`` is the live awake/asleep flag; ``last`` is a compact summary of the
        most recent completed sleep (or ``None`` before the first), so the dashboard
        and REPL can show "last sleep: consolidated N facts, self-model vN, ✓".
        """
        return serialize_sleep_block(
            asleep=asleep,
            full_agency=self._full_agency,
            last=sleep_summary_from_report(self._last_sleep),
        )

    def _apply_affective_context(self, ctx: CycleContext) -> None:
        """Hand the tick's mood/drives to Attention, Recall, and the Narrator.

        Optional capability (FC-7): each collaborator gets the context only if it
        exposes the setter, so a bare stage or a test double is untouched. This is
        what makes affect *steer* cognition rather than decorate it.
        """
        bias = self._build_attention_bias(ctx.mood, ctx.drives)
        self._set_if_supported(self._attention, "set_bias", bias)
        self._set_if_supported(self._recall, "set_mood", ctx.mood)
        self._set_if_supported(self._narration, "set_mood", ctx.mood)

    @staticmethod
    def _set_if_supported(collaborator: object, method: str, value: object) -> None:
        setter = getattr(collaborator, method, None)
        if callable(setter):
            setter(value)

    def _build_attention_bias(
        self, mood: Mood | None, drives: Sequence[DriveReading]
    ) -> AttentionBias:
        """Build Attention's bias from arousal (sharpen) + unmet-drive kind pulls."""
        arousal = mood.arousal if mood is not None else AttentionBias().arousal
        kind_boosts: dict[str, float] = {}
        for reading in drives:
            if not reading.over_threshold or reading.drive == SLEEP_DRIVE:
                continue
            pull = self._attention_drive_weight * reading.urgency
            for kind in _DRIVE_KIND_RELEVANCE.get(reading.drive, ()):
                kind_boosts[kind] = kind_boosts.get(kind, 0.0) + pull
        return AttentionBias(arousal=arousal, kind_boosts=kind_boosts)

    def _modulate_interval(self, mood: Mood | None, drives: Sequence[DriveReading]) -> float:
        """Map mood/energy to the next tick interval, hard-bounded (the 3.12 guard).

        Arousal shortens the interval (excited → faster); the Energy drive's
        overshoot lengthens it (tired → slower, toward sleep). The result is
        clamped to ``[min, max]`` so no arousal can spin the loop and no tiredness
        can freeze it.
        """
        arousal = mood.arousal if mood is not None else AttentionBias().arousal
        energy_excess = 0.0
        for reading in drives:
            if reading.drive == SLEEP_DRIVE and reading.over_threshold:
                energy_excess = reading.urgency
        interval = (
            self._interval
            * (1.0 + self._tired_slowdown * energy_excess)
            / (1.0 + self._arousal_speedup * arousal)
        )
        return min(self._max_interval, max(self._min_interval, interval))

    async def _deliberate(self, ctx: CycleContext) -> None:
        """DELIBERATE — arbitrate to the active goal and plan an internal action.

        Fills the Phase-2 stub in place (FC-7). Arbitration runs every tick;
        Deliberation only *plans* an action when its cadence is due, so the heavy
        step stays bounded. Phase 6 will insert a real Conscience at CHECK and a
        tool belt at ACT; here the action is internal (reflect/recall/…).

        Suspended in post-sleep degraded mode (SPEC §9.3): a failed wake self-check
        gates autonomous action off, so Johnny doesn't arbitrate/plan/act on a
        possibly-corrupted self-model — he keeps perceiving, appraising, and
        narrating, but stays still until a later wake self-check restores agency.
        """
        if self._deliberation is None or not self._full_agency:
            return
        result = await self._deliberation.deliberate(
            urges=ctx.urges, mood=ctx.mood, contents=ctx.contents
        )
        ctx.goal = result.goal
        ctx.action = result.action
        if ctx.goal is not None:
            await self._emit(
                ctx,
                "deliberation",
                "goal.active",
                {
                    "id": ctx.goal.id,
                    "source": ctx.goal.source,
                    "description": ctx.goal.description,
                    "priority": round(ctx.goal.priority, 4),
                },
                stage="deliberate",
            )

    async def _check(self, ctx: CycleContext) -> None:
        """CHECK — the Conscience vets a proposed tool action before it can run (FC-7/FC-9).

        Phase 6a's effector substrate: a *tool* action (one Deliberation proposed
        via the registry) is resolved + vetted here against Johnny's values; the
        verdict rides forward to ACT. Internal actions (reflect/recall/…) keep their
        Phase-3 path and are not vetted here. CHECK no-ops when there's no tool
        action, no dispatch wired, or agency is suspended — exactly as before.
        """
        if not self._full_agency or self._dispatch is None:
            return
        action = ctx.action
        if action is None or not action.is_tool_action:
            return
        assert action.tool is not None  # is_tool_action guarantees this
        proposed = ProposedAction(
            tool=action.tool,
            args=dict(action.tool_args),
            goal_id=action.goal_id,
            goal_description=action.description,
        )
        ctx.vetted = await self._dispatch.vet(proposed, contents=ctx.contents)

    async def _act(self, ctx: CycleContext) -> None:
        """ACT — execute the planned action; the single dispatch point audits it (FC-5).

        Bounded to one action per tick. A **tool** action runs through the vetted +
        audited effector dispatch (the Conscience already ran at CHECK); an
        **internal** action is executed by Deliberation and broadcast on the bus, as
        in Phase 3. Either way the satisfaction events feed the *next* APPRAISE, so
        acting eases the drive — the feedback that closes the autonomy loop.

        Gated by the full-agency flag (SPEC §9.3) — defence in depth: in degraded
        mode nothing is dispatched.
        """
        if not self._full_agency:
            return
        if ctx.action is None or ctx.goal is None:
            return
        if ctx.action.is_tool_action:
            await self._dispatch_tool_action(ctx)
            return
        # Internal action — Phase-3 path (Deliberation executes + resolves the goal).
        if self._deliberation is None:
            return
        outcome = await self._deliberation.act(ctx.action, ctx.goal, ctx.contents)
        await self._dispatch_action(
            {
                "action": ctx.action.kind,
                "goal_id": ctx.goal.id,
                "goal_source": ctx.goal.source,
                "summary": outcome.summary,
                "success": outcome.success,
            }
        )
        for event in outcome.drive_events:
            self.enqueue_drive_event(event)

    async def _dispatch_tool_action(self, ctx: CycleContext) -> None:
        """Run a vetted tool action through the effector dispatch (FC-5), then settle.

        ``commit`` runs the tool only if the CHECK verdict allowed it, writes the
        append-only ``action_log`` row, and emits the outcome on the bus. The goal is
        then resolved and drive-satisfaction events enqueued — but only on a real
        run (a veto eases nothing).
        """
        if self._dispatch is None or ctx.vetted is None or ctx.goal is None:
            return
        outcome = await self._dispatch.commit(ctx.vetted)
        success = outcome.ran and (outcome.result.success if outcome.result is not None else False)
        # Goal resolution lives in Deliberation (it owns the GoalStore); call it only
        # when supported, so a bare DeliberationStage double isn't required to have it.
        settle = getattr(self._deliberation, "settle_tool_action", None)
        if callable(settle):
            for event in await settle(ctx.goal, summary=outcome.summary, success=success):
                self.enqueue_drive_event(event)

    async def _dispatch_action(self, action: dict[str, object]) -> None:
        """Broadcast an internal action's outcome on the bus (FC-5 seam, internal path).

        Tool/effector actions go through the ``EffectorDispatch`` (vet → run → audit
        → emit); this is the lighter seam for *internal* cognition (reflect/recall/…),
        which has no tool to run and so records its intent on the bus for the live
        stream + ``/audit``.
        """
        await self._workspace.broadcast(
            WorkspaceEvent(module="effectors", type="action.dispatched", payload=action)
        )

    # ── helpers ────────────────────────────────────────────────────────────────

    async def _emit(
        self,
        ctx: CycleContext,
        module: str,
        event_type: str,
        payload: dict[str, object],
        *,
        stage: str | None,
    ) -> None:
        """Broadcast a stage output on the bus; a broadcast failure degrades the
        owning stage rather than the tick."""
        try:
            await self._workspace.broadcast(
                WorkspaceEvent(module=module, type=event_type, payload=payload)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if stage is not None:
                ctx.stage_errors[stage] = f"broadcast failed: {exc}"
            _log.warning("cycle.broadcast.failed", event_type=event_type, error=str(exc))


# ── state-surface serializers (the SINGLE source of the /ws/state frame shape) ──
#
# These build the consolidated state payload. The cycle calls ``serialize_state``
# from each tick's ``ctx`` (and from the awake↔asleep transitions); the
# ``GET /api/v1/state`` endpoint calls the SAME function over current state read
# off the repos. One projection ⇒ the live WS frame and the REST snapshot can't
# drift (FC-8) — do not duplicate this shape anywhere.


def serialize_drive(drive: DriveReading) -> dict[str, object]:
    """One drive's compact state for the snapshot."""
    return {
        "drive": drive.drive,
        "value": round(drive.value, 4),
        "setpoint": round(drive.setpoint, 4),
        "threshold": round(drive.threshold, 4),
        "over_threshold": drive.over_threshold,
    }


def serialize_mood(mood: Mood) -> dict[str, object]:
    """Johnny's mood as the state surface carries it (valence/arousal + tags)."""
    return {
        "valence": round(mood.valence, 4),
        "arousal": round(mood.arousal, 4),
        "emotions": {e: round(v, 4) for e, v in mood.emotions.items()},
        "descriptor": mood.descriptor(),
        "mood_id": mood.id,
    }


def serialize_sleep_block(
    *, asleep: bool, full_agency: bool, last: dict[str, object] | None
) -> dict[str, object]:
    """The sleep block on every snapshot — awake/asleep, the agency gate, last sleep."""
    return {"asleep": asleep, "full_agency": full_agency, "last": last}


def serialize_state(
    *,
    tick: int,
    drives: Sequence[DriveReading],
    mood: Mood | None,
    goals: Sequence[Goal],
    interval: float,
    sleep: dict[str, object],
) -> dict[str, object]:
    """Build the consolidated ``/ws/state`` frame / ``GET /api/v1/state`` payload.

    ``mood`` is ``None`` until Johnny has appraised one (a fresh Mind serialises a
    null mood); ``goals`` is whatever the caller passes (the tick's active goal for
    the live frame, ``goals.active()`` for the REST snapshot); ``sleep`` is a
    pre-built block (see ``serialize_sleep_block``).
    """
    return {
        "tick": tick,
        "drives": [serialize_drive(d) for d in drives],
        "mood": serialize_mood(mood) if mood is not None else None,
        "goals": goals_to_payload(list(goals)),
        "interval": round(interval, 3),
        "sleep": sleep,
    }


def _sleep_summary_fields(
    *,
    trigger: str,
    ended_at: datetime | None,
    facts_written: int,
    episodes_decayed: int,
    facts_merged: int,
    self_model_version: int | None,
    self_check_ok: bool | None,
    degraded_stages: list[str],
) -> dict[str, object]:
    """The shared last-sleep summary shape (built identically from a report or a log)."""
    return {
        "trigger": trigger,
        "ended_at": ended_at.isoformat() if ended_at else None,
        "facts_written": facts_written,
        "episodes_decayed": episodes_decayed,
        "facts_merged": facts_merged,
        "self_model_version": self_model_version,
        "self_check_ok": self_check_ok,
        "degraded_stages": degraded_stages,
    }


def sleep_summary_from_report(report: SleepReport | None) -> dict[str, object] | None:
    """Last-sleep summary from the in-memory ``SleepReport`` (the live WS frame)."""
    if report is None:
        return None
    return _sleep_summary_fields(
        trigger=report.trigger,
        ended_at=report.ended_at,
        facts_written=report.facts_written,
        episodes_decayed=report.episodes_decayed,
        facts_merged=report.facts_merged,
        self_model_version=report.self_model_version,
        self_check_ok=report.self_check_ok,
        degraded_stages=report.degraded_stages,
    )


def sleep_summary_from_log(log: SleepLog | None) -> dict[str, object] | None:
    """Last-sleep summary from a persisted ``SleepLog`` row (the REST snapshot).

    Produces the EXACT same shape as ``sleep_summary_from_report`` — the
    ``degraded_stages`` list lives in ``notes['degraded']`` on the persisted row.
    """
    if log is None:
        return None
    degraded = log.notes.get("degraded", [])
    return _sleep_summary_fields(
        trigger=log.trigger,
        ended_at=log.ended_at,
        facts_written=log.facts_written,
        episodes_decayed=log.episodes_decayed,
        facts_merged=log.facts_merged,
        self_model_version=log.self_model_version,
        self_check_ok=log.self_check_ok,
        degraded_stages=degraded if isinstance(degraded, list) else [],
    )
