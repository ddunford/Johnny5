"""Sleep — Johnny's offline life: consolidate, grow, back up, and wake intact.

Sleep is a **phase the run loop enters between ticks** (FC-7), not a tick stage:
normal ticking pauses, the offline pipeline runs, then the heartbeat resumes. This
module owns:

* ``WakeSelfCheck`` — the continuity safeguard run on wake (``SPEC §9.3``). The
  immutable Core identity anchor (name + prime directive) is the **trusted
  reference**: because the anchor cannot drift, the check trips when the
  *refreshed self-model* diverges from it (renamed Johnny, or unparseable/empty),
  not when the anchor "changes". It also confirms drives are within ``[0, 1]``. On
  failure full agency is **not** resumed (degrade + alert). It only *reads* the
  anchor — never writes it (FC-1).

* ``SleepCycle`` — the bounded offline orchestrator + awake↔asleep state machine.
  Between ticks the run loop asks ``sleep_trigger`` whether to sleep (Energy over
  threshold, or an every-N-ticks cadence); when it does, ``sleep()`` runs the
  pipeline (consolidate → decay/merge → self-model refresh → metacognition review →
  backup → restore-energy → wake self-check) **with per-stage isolation** so a
  failed stage degrades only itself and the loop never wedges asleep, and
  **bounded** (one sleep at a time; consolidation LLM calls capped by the
  ``Consolidator``) so an idle Johnny can't run up cloud spend through repeated
  sleeps. Each sleep opens + closes a ``sleep_log`` row for observability.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from brain.affect.agent import MoodRepository, MoodRow
from brain.drives.engine import (
    EVENT_PERSISTENCE_CONFIRMED,
    EVENT_REST,
    DriveEngine,
    DriveEvent,
    DriveReading,
    Urge,
)
from brain.goals.store import STATUS_ABANDONED, STATUS_RESOLVED, GoalRow
from brain.memory.base import utcnow
from brain.memory.consolidator import Consolidator
from brain.memory.decay import MemoryDecay
from brain.memory.episodic import EpisodeRepository
from brain.memory.semantic import SemanticFact, SemanticMemory
from brain.memory.snapshot import MemorySnapshot
from brain.metacognition.agent import Metacognition, Review, ReviewWindow
from brain.self_model.agent import ReflectionInputs, SelfModel
from brain.self_model.store import IdentityDoc
from core.identity_anchor import IdentityAnchor, load_identity_anchor
from foundation.config import get_settings
from foundation.db import Base, Repository, session_scope
from foundation.observability import get_logger

_log = get_logger("brain.sleep")

# The individual wake checks (stable names so the REPL / alert can reference them).
CHECK_SELF_MODEL_PRESENT = "self_model_present"
CHECK_ANCHOR_CONSISTENCY = "anchor_consistency"
CHECK_DRIVE_RANGES = "drive_ranges"

# Why a sleep was triggered (recorded on ``sleep_log.trigger``).
TRIGGER_ENERGY = "energy"
TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"

SLEEP_LOG_TABLE = "sleep_log"


# ── wake self-check ────────────────────────────────────────────────────────────


class CheckFinding(BaseModel):
    """One probe's result — ``ok`` plus a human-readable detail for the alert/REPL."""

    check: str
    ok: bool
    detail: str


class CheckResult(BaseModel):
    """The wake self-check verdict — ``ok`` only if every finding passed."""

    ok: bool
    findings: list[CheckFinding] = Field(default_factory=list)

    @property
    def failures(self) -> list[CheckFinding]:
        """The findings that failed (drives the degrade + alert path)."""
        return [f for f in self.findings if not f.ok]


class WakeSelfCheck:
    """Verify the self-model + Core invariants are intact before resuming full agency.

    Reads the immutable anchor (the trusted reference) and compares the live
    self-model + drive state against it. Pure of side effects on the Core — it never
    writes the anchor (FC-1). Collaborators are injected so tests can feed a tampered
    self-model or a deliberately-mismatched anchor and assert the gate trips.
    """

    def __init__(
        self,
        *,
        self_model: SelfModel | None = None,
        drives: DriveEngine | None = None,
        anchor: IdentityAnchor | None = None,
    ) -> None:
        self._self_model = self_model or SelfModel()
        self._drives = drives or DriveEngine()
        # Read the Core anchor once (read-only — the trusted reference, FC-1).
        self._anchor = anchor or load_identity_anchor()

    async def verify(self) -> CheckResult:
        """Run every probe and return the combined verdict (``ok`` iff all pass)."""
        doc = await self._self_model.current()
        readings = await self._drives.current()

        findings = [
            self._check_self_model_present(doc.self_model_doc),
            self._check_anchor_consistency(doc.name),
            self._check_drive_ranges(readings),
        ]
        ok = all(f.ok for f in findings)
        if not ok:
            _log.warning(
                "sleep.wake_self_check.failed",
                failures=[f.check for f in findings if not f.ok],
            )
        return CheckResult(ok=ok, findings=findings)

    # ── the probes ───────────────────────────────────────────────────────────

    @staticmethod
    def _check_self_model_present(self_model_doc: str) -> CheckFinding:
        """The refreshed self-model must be parseable and non-empty."""
        present = bool(self_model_doc.strip())
        return CheckFinding(
            check=CHECK_SELF_MODEL_PRESENT,
            ok=present,
            detail="self-model doc present" if present else "self-model doc is empty",
        )

    def _check_anchor_consistency(self, name: str) -> CheckFinding:
        """The self-model must not have drifted off its anchored identity (name).

        The anchor's name is immutable, so a mismatch means the *self-model* diverged
        (a corrupted refresh or a tampered row) — a real continuity failure.
        """
        consistent = name == self._anchor.name
        return CheckFinding(
            check=CHECK_ANCHOR_CONSISTENCY,
            ok=consistent,
            detail=(
                f"self-model name {name!r} matches the anchor"
                if consistent
                else f"self-model name {name!r} diverged from anchor {self._anchor.name!r}"
            ),
        )

    @staticmethod
    def _check_drive_ranges(readings: Sequence[DriveReading]) -> CheckFinding:
        """Every drive's pressure must be a finite value within ``[0, 1]``."""
        out_of_range = [r.drive for r in readings if not (0.0 <= r.value <= 1.0)]
        ok = not out_of_range
        return CheckFinding(
            check=CHECK_DRIVE_RANGES,
            ok=ok,
            detail="all drives within [0,1]" if ok else f"drives out of range: {out_of_range}",
        )


# ── sleep_log persistence ──────────────────────────────────────────────────────


class SleepLogRow(Base):
    """The ``sleep_log`` table — one row per sleep (open on enter, closed on wake)."""

    __tablename__ = SLEEP_LOG_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    facts_written: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    episodes_decayed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    facts_merged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    self_model_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    self_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")


class SleepLog(BaseModel):
    """A sleep_log row, decoupled from the ORM/session (the REPL/state surface reads this)."""

    id: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    trigger: str = TRIGGER_MANUAL
    facts_written: int = 0
    episodes_decayed: int = 0
    facts_merged: int = 0
    self_model_version: int | None = None
    snapshot_path: str | None = None
    self_check_ok: bool | None = None
    notes: dict[str, object] = Field(default_factory=dict)


def _row_to_sleep_log(row: SleepLogRow) -> SleepLog:
    return SleepLog(
        id=row.id,
        started_at=row.started_at,
        ended_at=row.ended_at,
        trigger=row.trigger,
        facts_written=row.facts_written,
        episodes_decayed=row.episodes_decayed,
        facts_merged=row.facts_merged,
        self_model_version=row.self_model_version,
        snapshot_path=row.snapshot_path,
        self_check_ok=row.self_check_ok,
        notes=dict(row.notes),
    )


class SleepLogRepository(Repository[SleepLogRow]):
    """Session-scoped persistence + latest-query for ``sleep_log`` rows."""

    model = SleepLogRow

    async def latest(self) -> SleepLogRow | None:
        """The most recent sleep (feeds the REPL/state "last sleep" summary)."""
        result = await self.session.execute(
            select(SleepLogRow).order_by(SleepLogRow.started_at.desc()).limit(1)
        )
        return result.scalars().first()


class SleepLogStore:
    """Open a sleep_log row on enter and close it on wake; read the latest."""

    def __init__(self, *, now_fn: Callable[[], datetime] = utcnow) -> None:
        self._now_fn = now_fn

    async def open(self, trigger: str, started_at: datetime) -> int:
        async with session_scope() as session:
            row = await SleepLogRepository(session).add(
                SleepLogRow(trigger=trigger, started_at=started_at)
            )
            return row.id

    async def close(self, sleep_id: int, *, ended_at: datetime, report: SleepReport) -> None:
        async with session_scope() as session:
            row = await SleepLogRepository(session).get(sleep_id)
            if row is None:
                return
            row.ended_at = ended_at
            row.facts_written = report.facts_written
            row.episodes_decayed = report.episodes_decayed
            row.facts_merged = report.facts_merged
            row.self_model_version = report.self_model_version
            row.snapshot_path = report.snapshot_path
            row.self_check_ok = report.self_check_ok
            row.notes = dict(report.notes)

    async def latest(self) -> SleepLog | None:
        async with session_scope() as session:
            row = await SleepLogRepository(session).latest()
        return _row_to_sleep_log(row) if row is not None else None


# ── the sleep report ───────────────────────────────────────────────────────────


class SleepReport(BaseModel):
    """The outcome of one sleep — returned to the run loop and mirrored to ``sleep_log``."""

    trigger: str
    started_at: datetime
    ended_at: datetime
    facts_written: int = 0
    episodes_decayed: int = 0
    facts_merged: int = 0
    self_model_version: int | None = None
    snapshot_path: str | None = None
    self_check_ok: bool = False
    reflection: str = ""
    degraded_stages: list[str] = Field(default_factory=list)
    notes: dict[str, object] = Field(default_factory=dict)


# ── the sleep cycle ──────────────────────────────────────────────────────────


class SleepCycle:
    """The bounded offline pipeline + awake↔asleep state machine (FC-7).

    Collaborators are injected (defaults wire the production agents) so tests can
    feed stub routers / fakes and freeze the clock. ``now_fn`` makes the whole
    pipeline (sleep_log timestamps, energy restore, decay) deterministic.
    """

    def __init__(
        self,
        *,
        consolidator: Consolidator | None = None,
        decay: MemoryDecay | None = None,
        self_model: SelfModel | None = None,
        metacognition: Metacognition | None = None,
        snapshot: MemorySnapshot | None = None,
        drives: DriveEngine | None = None,
        wake_check: WakeSelfCheck | None = None,
        log_store: SleepLogStore | None = None,
        every_ticks: int | None = None,
        reflection_episodes: int | None = None,
        review_goals: int | None = None,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        settings = get_settings()
        self._consolidator = consolidator or Consolidator(SemanticMemory())
        self._decay = decay or MemoryDecay(now_fn=now_fn)
        self._self_model = self_model or SelfModel()
        self._metacognition = metacognition or Metacognition()
        self._snapshot = snapshot or MemorySnapshot()
        self._drives = drives or DriveEngine(now_fn=now_fn)
        self._wake_check = wake_check or WakeSelfCheck(
            self_model=self._self_model, drives=self._drives
        )
        self._log = log_store or SleepLogStore(now_fn=now_fn)
        self._every_ticks = every_ticks if every_ticks is not None else settings.sleep_every_ticks
        self._reflection_episodes = (
            reflection_episodes
            if reflection_episodes is not None
            else settings.self_model_recent_episodes
        )
        self._review_goals = (
            review_goals if review_goals is not None else settings.metacognition_recent_goals
        )
        self._now_fn = now_fn
        self._asleep = False

    @property
    def is_asleep(self) -> bool:
        return self._asleep

    def sleep_trigger(self, urges: Sequence[Urge], *, tick: int = 0) -> str | None:
        """Why Johnny should sleep now, or ``None``. Checked between ticks (FC-7).

        Energy over threshold (the ``is_sleep_signal`` urge) is the primary trigger;
        an optional every-N-ticks cadence is a fallback. Never triggers while already
        asleep (one sleep at a time).
        """
        if self._asleep:
            return None
        if any(u.is_sleep_signal for u in urges):
            return TRIGGER_ENERGY
        if self._every_ticks > 0 and tick > 0 and tick % self._every_ticks == 0:
            return TRIGGER_SCHEDULED
        return None

    async def sleep(
        self, *, trigger: str = TRIGGER_MANUAL, now: datetime | None = None, degraded_ticks: int = 0
    ) -> SleepReport | None:
        """Run the bounded offline pipeline once; return the report (``None`` if busy).

        Bounded: one sleep at a time (a second concurrent call is a no-op) and the
        consolidation LLM calls are capped by the ``Consolidator``. Per-stage
        isolated: a failed stage is recorded on the report's ``notes``/``degraded``
        and the pipeline still reaches restore-energy + wake — the loop never wedges
        asleep.
        """
        if self._asleep:
            _log.info("sleep.already_asleep")
            return None
        self._asleep = True
        started = now if now is not None else self._now_fn()
        notes: dict[str, object] = {}
        degraded: list[str] = []
        report = SleepReport(trigger=trigger, started_at=started, ended_at=started)
        sleep_id: int | None = None
        try:
            sleep_id = await self._log.open(trigger, started)
            _log.info("sleep.enter", trigger=trigger, sleep_id=sleep_id)

            facts = await self._stage("consolidate", self._consolidator.run(), degraded, notes)
            report.facts_written = len(facts) if facts else 0
            if facts:
                notes["consolidation_summary"] = [
                    f"{f.subject} {f.predicate} {f.object}" for f in facts
                ]

            decay = await self._stage("decay", self._decay.run(now=started), degraded, notes)
            if decay is not None:
                report.episodes_decayed = decay.episodes_decayed
                report.facts_merged = decay.facts_merged

            doc = await self._stage(
                "self_model", self._refresh_self_model(facts or []), degraded, notes
            )
            if doc is not None:
                report.self_model_version = doc.version

            review = await self._stage(
                "metacognition", self._review(degraded_ticks), degraded, notes
            )
            if review is not None:
                report.reflection = review.reflection

            path = await self._stage("backup", self._snapshot.snapshot(), degraded, notes)
            report.snapshot_path = str(path) if path is not None else None

            # Always runs (even after a degraded stage): restore energy, and ease
            # Continuity iff the backup landed (persistence_confirmed → Continuity falls).
            await self._stage(
                "restore_energy",
                self._restore_energy(report.snapshot_path is not None, started),
                degraded,
                notes,
            )

            check = await self._stage("self_check", self._wake_check.verify(), degraded, notes)
            report.self_check_ok = bool(check and check.ok)
            if check is not None and not check.ok:
                notes["self_check_failures"] = [f.check for f in check.failures]
        finally:
            report.ended_at = self._now_fn()
            notes["degraded"] = degraded
            report.notes = notes
            report.degraded_stages = degraded
            if sleep_id is not None:
                await self._log.close(sleep_id, ended_at=report.ended_at, report=report)
            self._asleep = False
            _log.info(
                "sleep.wake",
                trigger=trigger,
                self_check_ok=report.self_check_ok,
                degraded=report.degraded_stages,
                facts=report.facts_written,
                self_model_version=report.self_model_version,
            )
        return report

    async def latest_sleep(self) -> SleepLog | None:
        """The most recent sleep_log row (for ``/ws/state`` + the REPL)."""
        return await self._log.latest()

    # ── per-stage isolation ────────────────────────────────────────────────────

    async def _stage[T](
        self, name: str, coro: Awaitable[T], degraded: list[str], notes: dict[str, object]
    ) -> T | None:
        """Run one sleep stage; a failure degrades only it (the loop still wakes)."""
        try:
            return await coro
        except Exception as exc:  # graceful degradation — never wedge asleep
            degraded.append(name)
            notes[f"{name}_error"] = f"{type(exc).__name__}: {exc}"
            _log.warning("sleep.stage.degraded", stage=name, error=str(exc))
            return None

    # ── stage bodies ───────────────────────────────────────────────────────────

    async def _refresh_self_model(self, facts: Sequence[SemanticFact]) -> IdentityDoc:
        inputs = await self._build_reflection_inputs(facts)
        return await self._self_model.refresh(inputs)

    async def _review(self, degraded_ticks: int) -> Review:
        window = await self._build_review_window(degraded_ticks)
        return await self._metacognition.review(window)

    async def _restore_energy(self, snapshot_ok: bool, now: datetime) -> None:
        """Step the drives with REST (+ PERSISTENCE_CONFIRMED on a successful backup).

        REST eases Energy (the tiredness that triggered sleep); a confirmed backup
        eases Continuity (the felt "I won't be lost") — the loop closing between the
        safety snapshot and the drive. Stepping the shared engine is what the next
        tick reads as restored energy.
        """
        events = [DriveEvent(kind=EVENT_REST)]
        if snapshot_ok:
            events.append(DriveEvent(kind=EVENT_PERSISTENCE_CONFIRMED))
        await self._drives.step(events, now=now)

    # ── input gathering ──────────────────────────────────────────────────────

    async def _build_reflection_inputs(self, facts: Sequence[SemanticFact]) -> ReflectionInputs:
        async with session_scope() as session:
            episodes = await EpisodeRepository(session).recent(self._reflection_episodes)
            mood_row = await MoodRepository(session).latest()
        fact_lines = [f"{f.subject} {f.predicate} {f.object}" for f in facts]
        drives = await self._drives.current()
        return ReflectionInputs(
            recent_episodes=[e.content for e in episodes],
            semantic_facts=fact_lines,
            mood=_mood_descriptor(mood_row),
            drives=_drive_summary(drives),
        )

    async def _build_review_window(self, degraded_ticks: int) -> ReviewWindow:
        async with session_scope() as session:
            resolved = await _count_goals(session, STATUS_RESOLVED)
            abandoned = await _count_goals(session, STATUS_ABANDONED)
            recent = await _recent_goal_descriptions(session, self._review_goals)
            mood_row = await MoodRepository(session).latest()
        drives = await self._drives.current()
        return ReviewWindow(
            goals_resolved=resolved,
            goals_abandoned=abandoned,
            degraded_ticks=degraded_ticks,
            recent_goals=recent,
            mood=_mood_descriptor(mood_row),
            drives=_drive_summary(drives),
        )


# ── small query/format helpers ─────────────────────────────────────────────────


async def _count_goals(session: AsyncSession, status: str) -> int:
    result = await session.execute(
        select(func.count()).select_from(GoalRow).where(GoalRow.status == status)
    )
    return int(result.scalar_one())


async def _recent_goal_descriptions(session: AsyncSession, limit: int) -> list[str]:
    result = await session.execute(
        select(GoalRow.source, GoalRow.description).order_by(GoalRow.created_at.desc()).limit(limit)
    )
    return [f"{source}: {description}" for source, description in result.all()]


def _mood_descriptor(mood_row: MoodRow | None) -> str:
    if mood_row is None:
        return "unremarkable"
    return f"valence {mood_row.valence:+.2f}, arousal {mood_row.arousal:.2f}"


def _drive_summary(drives: Sequence[DriveReading]) -> str:
    over = [f"{d.drive} {d.value:.2f}" for d in drives if d.over_threshold]
    return ", ".join(over) if over else "at rest"
