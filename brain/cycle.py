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
from typing import Protocol, runtime_checkable

from brain.memory.working import WorkingMemory, WorkingMemoryItem
from brain.workspace import Workspace, WorkspaceEvent, WorkspaceItem
from foundation.observability import get_logger

_log = get_logger("brain.cycle")

# Minimum backoff after an unexpected tick error, so a persistent failure can't
# turn the loop into a hot spin even if the configured interval is tiny.
_ERROR_BACKOFF_SECONDS = 1.0


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


# ── per-tick state + report ────────────────────────────────────────────────────


@dataclass
class CycleContext:
    """Mutable scratchpad threaded through one tick's stages."""

    tick: int
    percepts: list[WorkspaceItem] = field(default_factory=list)
    contents: list[WorkspaceItem] = field(default_factory=list)
    thought: str | None = None
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
        working_memory: WorkingMemory | None = None,
        interval_seconds: float = 4.0,
        workspace_capacity: int = 7,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._workspace = workspace
        self._perception = perception
        self._attention = attention
        self._recall = recall
        self._narration = narration
        self._learning = learning
        self._working_memory = working_memory
        self._interval = interval_seconds
        self._capacity = workspace_capacity
        self._sleep = sleep_fn
        self._gate = CycleGate()
        self._running = False
        self._tick = 0

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
                    await self._sleep(self._interval)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _log.exception("cycle.tick.unexpected_error", tick=self._tick)
                    await self._sleep(max(self._interval, _ERROR_BACKOFF_SECONDS))
        finally:
            self._running = False
            _log.info("cycle.run.stop", ticks=self._tick)

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
        """APPRAISE — Affect + Drives update from percepts/decay. Stub until Phase 3."""

    async def _deliberate(self, ctx: CycleContext) -> None:
        """DELIBERATE — Planner picks an action for the active goal. Stub until Phase 6."""

    async def _check(self, ctx: CycleContext) -> None:
        """CHECK — Conscience vets the proposed action. Stub until Phase 6."""

    async def _act(self, ctx: CycleContext) -> None:
        """ACT — Effectors execute the approved action. Stub until Phase 6.

        No action is produced yet, but the dispatch seam (FC-5) is reached every
        tick so Phase 6 can wrap *all* actions through one audited point without
        moving the stage.
        """
        # When DELIBERATE/CHECK produce an approved action, it routes here:
        #   await self._dispatch_action(action)
        return

    async def _dispatch_action(self, action: dict[str, object]) -> None:
        """The single action dispatch + audit point (FC-5).

        Every Effector action — internal or external — will pass through here so
        the Conscience check and the Core audit log wrap them uniformly. Phase 6
        gives it real effectors; today it only records the intent on the bus.
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
