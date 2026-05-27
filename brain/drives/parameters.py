"""Load the drive parameters + satisfaction map from ``config/drives.toml``.

The parameters (setpoint/rates/threshold per drive) and the event→satisfaction
map are config, not code (FC-3): they are exactly what Johnny re-tunes about
himself at runtime. This module parses that TOML into typed objects the engine
consumes. Mirrors ``brain/llm/routing.py``'s loader pattern.
"""

from __future__ import annotations

import tomllib
from math import exp
from pathlib import Path

from pydantic import BaseModel, Field

from brain.memory.base import clamp01
from foundation.config import Settings

# The seven drives (``SPEC §6.1``). The config must define exactly these — a
# missing or unknown drive is a configuration error worth surfacing loudly.
CURIOSITY = "curiosity"
BOREDOM = "boredom"
CONNECTION = "connection"
MASTERY = "mastery"
COHERENCE = "coherence"
ENERGY = "energy"
CONTINUITY = "continuity"

DRIVE_NAMES: tuple[str, ...] = (
    CURIOSITY,
    BOREDOM,
    CONNECTION,
    MASTERY,
    COHERENCE,
    ENERGY,
    CONTINUITY,
)


class DriveParams(BaseModel, frozen=True):
    """Tunable parameters for one drive (rate-based homeostasis).

    ``value`` is *pressure* in ``[0, 1]``; between events it relaxes along a
    bounded first-order curve toward ``equilibrium`` and never overshoots it.
    """

    setpoint: float
    accrual_rate: float
    decay_rate: float
    threshold: float

    @property
    def kappa(self) -> float:
        """The combined relaxation rate constant (accrual + decay)."""
        return self.accrual_rate + self.decay_rate

    @property
    def equilibrium(self) -> float:
        """The idle pressure the drive settles toward if never satisfied.

        Derived from the homeostatic ODE ``dv/dt = accrual·(1−v) − decay·(v−setpoint)``.
        Above ``threshold`` ⇒ the drive fires on its own while idle; below ⇒ it
        only fires when events push it over (continuity stays grounded this way).
        """
        if self.kappa <= 0:
            return clamp01(self.setpoint)
        return clamp01((self.accrual_rate + self.decay_rate * self.setpoint) / self.kappa)

    def advance(self, value: float, dt: float) -> float:
        """Relax ``value`` toward ``equilibrium`` over ``dt`` seconds (pure).

        Bounded and monotonic: a single huge ``dt`` lands exactly at equilibrium,
        never past it — so an idle drive climbs toward its ceiling without a long
        downtime spuriously pinning it at 1.0.
        """
        if dt <= 0 or self.kappa <= 0:
            return clamp01(value)
        eq = self.equilibrium
        return clamp01(eq + (value - eq) * exp(-self.kappa * dt))


class DriveConfig(BaseModel):
    """The resolved drive configuration: per-drive params + the satisfaction map."""

    drives: dict[str, DriveParams]
    # event kind → {drive: signed weight}. Negative satisfies (lowers pressure);
    # positive raises it. Applied as ``weight × event_intensity``.
    satisfaction: dict[str, dict[str, float]] = Field(default_factory=dict)

    def params(self, drive: str) -> DriveParams:
        """Parameters for a drive (KeyError if the config omits it)."""
        return self.drives[drive]

    def deltas_for(self, event_kind: str) -> dict[str, float]:
        """The per-drive weights an event of this kind applies (empty if unmapped)."""
        return self.satisfaction.get(event_kind, {})


def load_drive_config(settings: Settings, path: str | Path | None = None) -> DriveConfig:
    """Parse ``drives.toml`` into a ``DriveConfig``, validating the seven drives."""
    drives_path = Path(path) if path is not None else Path(settings.drives_config_path)
    with drives_path.open("rb") as fh:
        raw = tomllib.load(fh)

    drives_raw = raw.get("drives", {})
    missing = [name for name in DRIVE_NAMES if name not in drives_raw]
    if missing:
        raise ValueError(f"drives config at {drives_path} is missing drives: {missing}")
    unknown = [name for name in drives_raw if name not in DRIVE_NAMES]
    if unknown:
        raise ValueError(f"drives config at {drives_path} defines unknown drives: {unknown}")

    drives = {name: DriveParams(**drives_raw[name]) for name in DRIVE_NAMES}
    satisfaction = {
        kind: {drive: float(weight) for drive, weight in mapping.items()}
        for kind, mapping in raw.get("satisfaction", {}).items()
    }
    return DriveConfig(drives=drives, satisfaction=satisfaction)
