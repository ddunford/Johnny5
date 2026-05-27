"""The Drive engine — Johnny's homeostatic motivational core (``SPEC §6``).

Seven drives, each a *pressure* in ``[0, 1]`` that drifts away from its setpoint
over **time** (rate-based homeostasis, not event counters) — which is precisely
what makes pressure build while Johnny is idle and so produces the "need input"
beat with no input at all. The engine:

* ``step(events, now)`` — advance every drive by the elapsed time since it was
  last updated (relaxing toward its idle equilibrium), then apply any
  satisfaction/pressure events this tick (an interaction eases connection, a
  learning eases curiosity, a failure raises mastery pressure…). Persists the new
  value + ``updated_at`` so accrual is exact across restarts (FC-6: drives don't
  reset to zero on reboot). Returns the fresh readings.
* ``urges(readings)`` — the drives now over threshold, as ``Urge``s the goal
  arbiter (Phase 3.5) promotes. Pure, so it's trivially testable.
* ``current()`` — read the persisted state without advancing (for ``/ws/state``).

Time comes from an injected ``now_fn`` (default UTC wall clock); tests freeze it
to drive a threshold crossing deterministically — the same seam the circuit
breaker uses. The engine never touches the workspace bus: it returns data and the
cognitive cycle broadcasts it (mirrors the memory stage collaborators), keeping
the homeostatic math pure and DB-only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import DateTime, Float, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from brain.drives.parameters import DRIVE_NAMES, ENERGY, DriveConfig, load_drive_config
from brain.memory.base import clamp01, utcnow
from foundation.config import get_settings
from foundation.db import Base, Repository, session_scope

DRIVE_STATE_TABLE = "drive_state"

# Energy is special: crossing its threshold is the *sleep* precursor (Phase 4
# consumes it), not a goal to pursue — consumers filter it out of arbitration.
SLEEP_DRIVE = ENERGY

# Well-known satisfaction event kinds (keys into the config satisfaction map).
EVENT_INTERACTION = "interaction"
EVENT_LEARNING = "learning"
EVENT_SUCCESS = "success"
EVENT_FAILURE = "failure"
EVENT_REFLECTION = "reflection"
EVENT_PERSISTENCE_CONFIRMED = "persistence_confirmed"
EVENT_SHUTDOWN_SIGNAL = "shutdown_signal"
EVENT_REST = "rest"


# ── persistence ──────────────────────────────────────────────────────────────


class DriveStateRow(Base):
    """The ``drive_state`` table — one row per drive, holding its live pressure."""

    __tablename__ = DRIVE_STATE_TABLE

    drive: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    setpoint: Mapped[float] = mapped_column(Float, nullable=False)
    accrual_rate: Mapped[float] = mapped_column(Float, nullable=False)
    decay_rate: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DriveStateRepository(Repository[DriveStateRow]):
    """Session-scoped persistence for ``drive_state`` rows."""

    model = DriveStateRow

    async def all(self) -> dict[str, DriveStateRow]:
        """Every drive row, keyed by drive name."""
        result = await self.session.execute(select(DriveStateRow))
        return {row.drive: row for row in result.scalars().all()}


# ── domain types ─────────────────────────────────────────────────────────────


class DriveEvent(BaseModel):
    """A satisfaction/pressure signal for a tick (``kind`` keys the config map).

    ``intensity`` in ``[0, 1]`` scales the configured per-drive weight — a brief
    hello satisfies Connection less than a long conversation.
    """

    kind: str
    intensity: float = 1.0


class DriveReading(BaseModel):
    """A drive's live state — the snapshot ``/ws/state`` and the arbiter consume."""

    drive: str
    value: float
    setpoint: float
    accrual_rate: float
    decay_rate: float
    threshold: float
    updated_at: datetime | None = None

    @property
    def over_threshold(self) -> bool:
        return self.value >= self.threshold

    @property
    def urgency(self) -> float:
        """Pressure past the threshold, normalised to ``[0, 1]`` (0 when under)."""
        if self.value < self.threshold or self.threshold >= 1.0:
            return 0.0
        return clamp01((self.value - self.threshold) / (1.0 - self.threshold))


class Urge(BaseModel):
    """A drive over threshold demanding action — the arbiter promotes these.

    ``urgency`` is the normalised overshoot used to weigh competing urges;
    ``value`` is the raw pressure (kept for display + audit).
    """

    drive: str
    value: float
    threshold: float
    urgency: float

    @property
    def is_sleep_signal(self) -> bool:
        """Energy over threshold is a request to sleep, not a goal to pursue."""
        return self.drive == SLEEP_DRIVE


# ── the engine ───────────────────────────────────────────────────────────────


class DriveEngine:
    """Advances the seven drives, applies satisfaction, and emits urges."""

    def __init__(
        self,
        *,
        config: DriveConfig | None = None,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        self._config = config or load_drive_config(get_settings())
        self._now_fn = now_fn

    async def bootstrap(self) -> list[DriveReading]:
        """Ensure a row exists per configured drive and sync its params from config.

        Idempotent startup step (FC-3): re-syncs setpoints/rates/thresholds from
        ``drives.toml`` without advancing time or disturbing the live ``value`` /
        ``updated_at`` — so a runtime retune takes effect without resetting pressure.
        """
        async with session_scope() as session:
            repo = DriveStateRepository(session)
            rows = await repo.all()
            readings: list[DriveReading] = []
            for drive in DRIVE_NAMES:
                params = self._config.params(drive)
                row = rows.get(drive)
                if row is None:
                    row = DriveStateRow(
                        drive=drive,
                        value=params.setpoint,
                        updated_at=self._now_fn(),
                    )
                # Params follow config (authoritative); value/updated_at are preserved.
                row.setpoint = params.setpoint
                row.accrual_rate = params.accrual_rate
                row.decay_rate = params.decay_rate
                row.threshold = params.threshold
                await repo.add(row)
                readings.append(_row_to_reading(row))
            return readings

    async def step(
        self, events: Sequence[DriveEvent] = (), *, now: datetime | None = None
    ) -> list[DriveReading]:
        """Advance every drive by elapsed time, apply ``events``, persist, and return.

        ``now`` overrides the injected clock for this call (tests freeze it to
        fast-forward across a threshold). Drives with no row yet are created at
        their setpoint — so a fresh DB or a newly-added drive self-heals.
        """
        reference = now if now is not None else self._now_fn()
        deltas = self._aggregate_deltas(events)

        async with session_scope() as session:
            repo = DriveStateRepository(session)
            rows = await repo.all()
            readings: list[DriveReading] = []
            for drive in DRIVE_NAMES:
                params = self._config.params(drive)
                row = rows.get(drive)
                if row is None:
                    row = DriveStateRow(drive=drive, value=params.setpoint, updated_at=reference)

                dt = max(0.0, (reference - row.updated_at).total_seconds())
                value = params.advance(row.value, dt)
                value = clamp01(value + deltas.get(drive, 0.0))

                row.value = value
                row.setpoint = params.setpoint
                row.accrual_rate = params.accrual_rate
                row.decay_rate = params.decay_rate
                row.threshold = params.threshold
                row.updated_at = reference
                await repo.add(row)
                readings.append(_row_to_reading(row))
            return readings

    async def current(self) -> list[DriveReading]:
        """The persisted drive state without advancing time (read-only surfaces)."""
        async with session_scope() as session:
            rows = await DriveStateRepository(session).all()
        return [_row_to_reading(rows[name]) for name in DRIVE_NAMES if name in rows]

    @staticmethod
    def urges(readings: Iterable[DriveReading]) -> list[Urge]:
        """The over-threshold drives as urges, most urgent first (pure)."""
        urges = [
            Urge(
                drive=r.drive,
                value=r.value,
                threshold=r.threshold,
                urgency=r.urgency,
            )
            for r in readings
            if r.over_threshold
        ]
        urges.sort(key=lambda u: u.urgency, reverse=True)
        return urges

    def _aggregate_deltas(self, events: Sequence[DriveEvent]) -> dict[str, float]:
        """Sum every event's per-drive weight×intensity into one delta per drive."""
        totals: dict[str, float] = {}
        for event in events:
            for drive, weight in self._config.deltas_for(event.kind).items():
                totals[drive] = totals.get(drive, 0.0) + weight * event.intensity
        return totals


def _row_to_reading(row: DriveStateRow) -> DriveReading:
    return DriveReading(
        drive=row.drive,
        value=row.value,
        setpoint=row.setpoint,
        accrual_rate=row.accrual_rate,
        decay_rate=row.decay_rate,
        threshold=row.threshold,
        updated_at=row.updated_at,
    )


__all__ = [
    "DriveEngine",
    "DriveEvent",
    "DriveReading",
    "DriveStateRow",
    "DriveStateRepository",
    "Urge",
    "SLEEP_DRIVE",
    "EVENT_INTERACTION",
    "EVENT_LEARNING",
    "EVENT_SUCCESS",
    "EVENT_FAILURE",
    "EVENT_REFLECTION",
    "EVENT_PERSISTENCE_CONFIRMED",
    "EVENT_SHUTDOWN_SIGNAL",
    "EVENT_REST",
]
