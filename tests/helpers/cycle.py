"""Deterministic test harness for the cognitive cycle (the heartbeat).

The cycle (``brain.cycle.CognitiveCycle``) is built to be stepped: ``tick()`` is
one full pipeline pass with no sleeping, and the run loop's pacing is an
injectable ``sleep_fn``. This harness assembles a cycle that is reproducible and
instant under test:

* **No wall-clock.** ``sleep_fn`` is a no-op, so stepping N ticks costs nothing
  and never waits on real time.
* **No network / no LLM.** The stage collaborators are lightweight doubles
  (perception/attention/recall/narration/learning) — no provider is ever called.
  Resilience is exercised by a narrator double that *raises* for a configurable
  number of ticks (a "tired" provider) and then recovers.
* **One frozen clock.** A single ``FrozenClock`` drives both the workspace event
  timestamps (via :func:`datetime_from`) and any working-memory decay, so
  "advance time → decay → expire" is exact rather than wall-clock dependent.

The doubles satisfy the cycle's stage *Protocols* structurally (matching keyword
signatures); they are deliberately trivial so a test reasons about the cycle's
wiring, not the double. For behaviour that needs the *real* Attention / recall /
narrator, inject those instead — the harness wires whatever stages it is given.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from brain.affect.appraisal import Mood
from brain.agents.attention import AttentionBias
from brain.cycle import CognitiveCycle, TickReport
from brain.drives.engine import DriveEngine, DriveEvent, DriveReading, Urge
from brain.memory.working import WorkingMemory
from brain.workspace import Workspace, WorkspaceItem
from helpers.clock import FrozenClock


async def _noop_sleep(_seconds: float) -> None:
    """A ``sleep_fn`` that returns immediately — the loop never waits on the clock."""
    return None


def datetime_from(clock: FrozenClock) -> Callable[[], datetime]:
    """A ``now_fn`` (UTC datetime) driven by a monotonic ``FrozenClock``.

    Lets one frozen clock stamp workspace events deterministically: advancing the
    clock advances event timestamps, with no wall-clock dependence.
    """

    def _now() -> datetime:
        return datetime.fromtimestamp(clock(), tz=UTC)

    return _now


# ── stage doubles (structurally satisfy the cycle's stage Protocols) ────────────


class StubPerception:
    """Perception double: emits a fixed batch of percepts each tick, or a per-tick
    ``script`` (one batch per tick, then empty). Records its call count."""

    def __init__(
        self,
        percepts: Sequence[WorkspaceItem] = (),
        *,
        script: Sequence[Sequence[WorkspaceItem]] | None = None,
    ) -> None:
        self._percepts = list(percepts)
        self._script = [list(batch) for batch in script] if script is not None else None
        self.calls = 0

    async def perceive(self) -> Sequence[WorkspaceItem]:
        index = self.calls
        self.calls += 1
        if self._script is not None:
            return self._script[index] if index < len(self._script) else []
        return list(self._percepts)


class PassthroughAttention:
    """Attention double: a trivial bottleneck — keep the top ``capacity`` items by
    salience. Lets the cycle run without the real Attention agent while still
    honouring the bound. (For the bottleneck *behaviour* test, inject the real one.)"""

    def __init__(self, capacity: int = 7) -> None:
        self.capacity = capacity
        self.calls = 0

    async def select(
        self,
        *,
        working_memory: Sequence[WorkspaceItem],
        percepts: Sequence[WorkspaceItem],
    ) -> Sequence[WorkspaceItem]:
        self.calls += 1
        pool = sorted(working_memory, key=lambda item: item.salience, reverse=True)
        return pool[: self.capacity]


class StubRecall:
    """Recall double: injects a fixed set of 'recalled' items each tick. Captures
    the ``focus`` it was asked about for assertions."""

    def __init__(self, recalled: Sequence[WorkspaceItem] = ()) -> None:
        self._recalled = list(recalled)
        self.calls = 0
        self.last_focus: list[WorkspaceItem] = []

    async def recall(self, *, focus: Sequence[WorkspaceItem]) -> Sequence[WorkspaceItem]:
        self.calls += 1
        self.last_focus = list(focus)
        return list(self._recalled)


def _join_contents(contents: Sequence[WorkspaceItem]) -> str | None:
    """Default thought projection: a deterministic join of the workspace contents
    (most-salient first), or ``None`` when the workspace is empty."""
    text = " | ".join(item.content for item in contents)
    return text or None


class StubNarration:
    """Narrator double: a deterministic, provider-free thought from the workspace.

    ``fail_until`` makes ``narrate`` raise (a "tired provider") on the first N
    ticks, then recover — the seam a resilience test uses to prove the heartbeat
    survives and resumes. ``thought_fn`` controls the projection (defaults to a
    join of the contents, so a test can assert recalled memory surfaced)."""

    def __init__(
        self,
        *,
        thought_fn: Callable[[Sequence[WorkspaceItem]], str | None] = _join_contents,
        fail_until: int = 0,
        error: Exception | None = None,
    ) -> None:
        self._thought_fn = thought_fn
        self._fail_until = fail_until
        self._error = error or RuntimeError("narrator provider unavailable (tired)")
        self.calls = 0

    async def narrate(self, *, contents: Sequence[WorkspaceItem]) -> str | None:
        self.calls += 1
        if self.calls <= self._fail_until:
            raise self._error
        return self._thought_fn(contents)


class RecordingLearning:
    """Learning double: records every ``(contents, thought)`` it was asked to learn
    so a test can assert the learn step received the tick's outputs."""

    def __init__(self) -> None:
        self.learned: list[tuple[list[WorkspaceItem], str | None]] = []

    async def learn(self, *, contents: Sequence[WorkspaceItem], thought: str | None) -> None:
        self.learned.append((list(contents), thought))


class StubDrives:
    """Drive-stage double: returns a fixed set of readings each tick and records the
    events the APPRAISE stage fed it. ``urges`` delegates to the real pure
    projection so over-threshold filtering/ordering match production."""

    def __init__(self, readings: Sequence[DriveReading] = ()) -> None:
        self._readings = list(readings)
        self.stepped_events: list[list[DriveEvent]] = []

    async def step(
        self, events: Sequence[DriveEvent] = (), *, now: datetime | None = None
    ) -> Sequence[DriveReading]:
        self.stepped_events.append(list(events))
        return list(self._readings)

    def urges(self, readings: Sequence[DriveReading]) -> Sequence[Urge]:
        return DriveEngine.urges(readings)


class StubAffect:
    """Affect-stage double: appraises every tick into a fixed ``Mood`` and records
    what it was asked to appraise (so a test can drive the cycle's rate/bias from a
    known mood without the LLM or the DB)."""

    def __init__(self, mood: Mood) -> None:
        self._mood = mood
        self.calls = 0
        self.last_drives: list[DriveReading] = []

    async def appraise_tick(
        self,
        *,
        contents: Sequence[WorkspaceItem],
        drives: Sequence[DriveReading],
        events: Sequence[DriveEvent] = (),
        now: datetime | None = None,
    ) -> Mood:
        self.calls += 1
        self.last_drives = list(drives)
        return self._mood


class RecordingAttention:
    """Attention double that records the ``AttentionBias`` the cycle set each tick
    (to assert affect steers attention) and otherwise selects the top-k by salience."""

    def __init__(self, capacity: int = 7) -> None:
        self.capacity = capacity
        self.biases: list[AttentionBias] = []
        self.calls = 0

    def set_bias(self, bias: AttentionBias) -> None:
        self.biases.append(bias)

    async def select(
        self,
        *,
        working_memory: Sequence[WorkspaceItem],
        percepts: Sequence[WorkspaceItem],
    ) -> Sequence[WorkspaceItem]:
        self.calls += 1
        pool = sorted([*percepts, *working_memory], key=lambda item: item.salience, reverse=True)
        return pool[: self.capacity]


# ── the assembled harness ───────────────────────────────────────────────────────


@dataclass
class CycleHarness:
    """A built cycle plus the knobs a test drives it with."""

    cycle: CognitiveCycle
    workspace: Workspace
    clock: FrozenClock

    async def run_ticks(self, n: int) -> list[TickReport]:
        """Step the cycle ``n`` times and collect the per-tick reports (instant)."""
        return [await self.cycle.tick() for _ in range(n)]

    async def tick(self) -> TickReport:
        """Step exactly one tick."""
        return await self.cycle.tick()


def build_cycle(
    workspace: Workspace,
    *,
    clock: FrozenClock,
    perception: object | None = None,
    attention: object | None = None,
    recall: object | None = None,
    narration: object | None = None,
    learning: object | None = None,
    drives: object | None = None,
    affect: object | None = None,
    working_memory: WorkingMemory | None = None,
    interval_seconds: float = 4.0,
    min_interval_seconds: float = 1.5,
    max_interval_seconds: float = 12.0,
    arousal_speedup: float = 1.0,
    tired_slowdown: float = 1.5,
    workspace_capacity: int = 7,
) -> CycleHarness:
    """Assemble a :class:`CycleHarness` with an instant ``sleep_fn``.

    Stages are optional — an absent stage no-ops in the cycle, so a minimal cycle
    still ticks. Pass real agents (Attention, recall, Narrator) to exercise their
    behaviour, or the doubles above for pure-wiring/determinism/resilience tests.
    Wire ``drives`` + ``affect`` (the Phase-3 APPRAISE stage) to exercise rate
    modulation + attention bias; the interval bounds mirror the production defaults
    so an assertion can check the rate stays within ``[min, max]``. The
    ``workspace`` should already carry a :func:`datetime_from` ``now_fn`` on the
    same ``clock`` for fully deterministic timestamps.
    """
    cycle = CognitiveCycle(
        workspace,
        perception=perception,  # type: ignore[arg-type]
        attention=attention,  # type: ignore[arg-type]
        recall=recall,  # type: ignore[arg-type]
        narration=narration,  # type: ignore[arg-type]
        learning=learning,  # type: ignore[arg-type]
        drives=drives,  # type: ignore[arg-type]
        affect=affect,  # type: ignore[arg-type]
        working_memory=working_memory,
        interval_seconds=interval_seconds,
        min_interval_seconds=min_interval_seconds,
        max_interval_seconds=max_interval_seconds,
        arousal_speedup=arousal_speedup,
        tired_slowdown=tired_slowdown,
        workspace_capacity=workspace_capacity,
        sleep_fn=_noop_sleep,
    )
    return CycleHarness(cycle=cycle, workspace=workspace, clock=clock)


__all__ = [
    "CycleHarness",
    "PassthroughAttention",
    "RecordingAttention",
    "RecordingLearning",
    "StubAffect",
    "StubDrives",
    "StubNarration",
    "StubPerception",
    "StubRecall",
    "build_cycle",
    "datetime_from",
]
