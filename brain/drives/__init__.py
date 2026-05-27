"""Drives — Johnny's homeostatic motivational core (``SPEC §6``).

Seven rate-based drives whose pressure builds over time and is eased by events;
when one crosses its threshold it emits an ``Urge`` the goal arbiter can promote.
This is the mechanism that makes Johnny *want* — the autonomy loop's source.
"""

from __future__ import annotations

from brain.drives.engine import (
    SLEEP_DRIVE,
    DriveEngine,
    DriveEvent,
    DriveReading,
    DriveStateRepository,
    DriveStateRow,
    Urge,
)
from brain.drives.parameters import DRIVE_NAMES, DriveConfig, DriveParams, load_drive_config

__all__ = [
    "DRIVE_NAMES",
    "SLEEP_DRIVE",
    "DriveConfig",
    "DriveEngine",
    "DriveEvent",
    "DriveParams",
    "DriveReading",
    "DriveStateRepository",
    "DriveStateRow",
    "Urge",
    "load_drive_config",
]
