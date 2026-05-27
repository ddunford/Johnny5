"""Wake self-check gates the resume of full agency (TC-4.10, SPEC §9.3).

On wake, ``WakeSelfCheck.verify()`` confirms Johnny is intact before normal ticking
resumes. The immutable Core anchor (name + prime directive) is the **trusted
reference**: because it cannot drift, the check trips when the *refreshed self-model*
diverges from it — renamed, or blanked/unparseable — or when a drive is out of
``[0, 1]``. On failure full agency must **not** resume (``ok=False`` → degrade +
alert). The check only *reads* the anchor; it never writes it (FC-1).

This suite drives the gate through its three outcomes:
* intact self-model + in-range drives → ``ok=True`` (resume);
* a blanked self-model doc → fails (does not wake into an empty identity);
* a self-model whose name contradicts the anchor → fails (and the anchor itself is
  untouched — read-only);
* an out-of-range drive → fails.

Tampering inserts a corrupt *latest* ``identity`` row, which ``current()`` returns
(``ensure_seeded`` only seeds when the table is empty). DB-backed → ``./ctl.sh test``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from brain.drives.engine import DriveEngine, DriveReading
from brain.self_model.agent import SelfModel
from brain.self_model.store import IdentityRow, IdentityStore
from brain.sleep import (
    CHECK_ANCHOR_CONSISTENCY,
    CHECK_DRIVE_RANGES,
    CHECK_SELF_MODEL_PRESENT,
    WakeSelfCheck,
)
from core.identity_anchor import JOHNNY_NAME, load_identity_anchor
from foundation.db import session_scope

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


async def _seed_identity_row(*, name: str, self_model_doc: str, version: int = 1) -> None:
    """Insert a corrupt/latest identity row directly (bypassing the anchor-grounded seed)."""
    async with session_scope() as session:
        session.add(
            IdentityRow(
                name=name,
                self_model_doc=self_model_doc,
                values=[],
                concerns=[],
                relationships={},
                version=version,
                created_at=_T0,
            )
        )


class _OutOfRangeDrives:
    """A DriveEngine double whose ``current()`` returns an out-of-range reading.

    The ``drive_state`` table has a CHECK constraint (``ck_drive_state_value_range``)
    that forbids persisting a value outside [0, 1] — the product's first line of
    defence. So an out-of-range value can't be written to the DB; this double feeds
    the wake check's drive-range probe an illegal reading directly to prove the probe
    (the belt-and-suspenders second line) trips on it."""

    async def current(self) -> Sequence[DriveReading]:
        return [
            DriveReading(
                drive="curiosity",
                value=1.5,  # out of [0, 1] — only reachable in-memory, never persisted
                setpoint=0.1,
                accrual_rate=0.0008,
                decay_rate=0.0002,
                threshold=0.65,
            )
        ]


def _failed_checks(result: object) -> set[str]:
    return {f.check for f in result.failures}  # type: ignore[attr-defined]


async def test_wake_self_check_passes_when_self_model_consistent_and_drives_in_range(
    sleep_db: AsyncEngine,
) -> None:
    """Intact: the anchor-grounded v1 self-model + bootstrapped (in-range) drives →
    the gate passes and full agency may resume."""
    await SelfModel().current()  # seeds the anchor-grounded v1 (name=Johnny)
    await DriveEngine().bootstrap()  # seeds the seven drives at their setpoints (in range)

    result = await WakeSelfCheck().verify()

    assert result.ok is True
    assert result.failures == []
    assert all(f.ok for f in result.findings)


async def test_wake_self_check_fails_on_a_blanked_self_model(sleep_db: AsyncEngine) -> None:
    """A blanked self-model doc trips the gate — Johnny does not wake into an empty
    identity; full agency does not resume."""
    await _seed_identity_row(name=JOHNNY_NAME, self_model_doc="   ")  # whitespace-only = empty
    await DriveEngine().bootstrap()

    result = await WakeSelfCheck().verify()

    assert result.ok is False
    assert CHECK_SELF_MODEL_PRESENT in _failed_checks(result)


async def test_wake_self_check_fails_when_self_model_name_contradicts_anchor(
    sleep_db: AsyncEngine,
) -> None:
    """A self-model whose name diverged from the immutable anchor trips the gate —
    and the anchor itself is never written (read-only, FC-1)."""
    await _seed_identity_row(name="NotJohnny", self_model_doc="I am someone else now.")
    await DriveEngine().bootstrap()

    result = await WakeSelfCheck().verify()

    assert result.ok is False
    assert CHECK_ANCHOR_CONSISTENCY in _failed_checks(result)
    # The check only READ the anchor — the trusted reference is untouched.
    assert load_identity_anchor().name == JOHNNY_NAME


async def test_wake_self_check_fails_on_an_out_of_range_drive(sleep_db: AsyncEngine) -> None:
    """A drive pressure outside [0, 1] (corrupt state) trips the gate even with an
    otherwise-intact self-model."""
    await SelfModel().current()  # valid self-model, so only the drive check can fail

    # Inject a drive engine that yields an out-of-range reading (it can't be persisted
    # — the DB CHECK constraint forbids it — so we feed the probe directly).
    result = await WakeSelfCheck(drives=_OutOfRangeDrives()).verify()  # type: ignore[arg-type]

    assert result.ok is False
    assert CHECK_DRIVE_RANGES in _failed_checks(result)


async def test_wake_self_check_on_a_fresh_being_seeds_and_passes(sleep_db: AsyncEngine) -> None:
    """A first wake with no prior state: ``verify`` self-seeds the anchor-grounded v1
    via ``current()`` and passes (no drives yet → trivially in range)."""
    result = await WakeSelfCheck().verify()

    assert result.ok is True
    # And it left an anchor-grounded v1 self-model behind (named for the anchor).
    current = await IdentityStore().current()
    assert current is not None
    assert current.name == JOHNNY_NAME
