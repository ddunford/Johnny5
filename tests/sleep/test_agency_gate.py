"""The wake self-check GATES full-agency resume — the gate bites (TC-4.10, SPEC §9.3).

`test_wake_self_check.py` asserts the *verdict* (`verify()` → ok/findings). This
suite asserts the loop's **response** to that verdict: a failed wake self-check must
actually suspend autonomous action, not merely log. (The security review found the
verdict was computed but never acted on — Johnny resumed full agency after a FAILED
check. This is the regression guard for that fix.)

`CognitiveCycle._full_agency` gates both DELIBERATE and ACT. It drops to False from:
* a **sleep** whose `self_check_ok` is False (`_maybe_sleep`), and
* a **startup** wake self-check that fails (`apply_wake_check`, the restart path) —
and is restored by a later passing check. While False, `tick()` still perceives /
appraises / narrates (the heartbeat lives + stays observable) but makes **zero**
deliberate/act calls, and a `sleep.degraded` alert is broadcast.

Deterministic via a `FakeSleep` (no real pipeline/router) + a deliberation spy that
counts calls. DB+Redis-backed (the cycle broadcasts to the workspace) → `./ctl.sh test`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from brain.affect.appraisal import Mood
from brain.agents.deliberation import Action, ActionOutcome, DeliberationResult
from brain.cycle import CognitiveCycle
from brain.drives.engine import SLEEP_DRIVE, Urge
from brain.goals.store import Goal
from brain.sleep import CHECK_ANCHOR_CONSISTENCY, CheckFinding, CheckResult, SleepReport
from brain.workspace import Workspace, WorkspaceItem

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_SLEEP_DEGRADED = "sleep.degraded"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _energy_urges() -> list[Urge]:
    """An Energy over-threshold urge — the sleep trigger."""
    return [Urge(drive=SLEEP_DRIVE, value=0.9, threshold=0.8, urgency=0.5)]


class _DeliberationSpy:
    """Counts the deliberate/act calls the cycle makes — to prove the gate suspends them."""

    def __init__(self) -> None:
        self.deliberated = 0
        self.acted = 0

    async def deliberate(
        self,
        *,
        urges: Sequence[Urge],
        mood: Mood | None,
        contents: Sequence[WorkspaceItem],
        now: datetime | None = None,
    ) -> DeliberationResult:
        self.deliberated += 1
        return DeliberationResult(goal=None, action=None)  # no goal → ACT no-ops anyway

    async def act(
        self, action: Action, goal: Goal, contents: Sequence[WorkspaceItem]
    ) -> ActionOutcome:
        self.acted += 1
        return ActionOutcome(action_kind=action.kind, success=True, summary="acted")


class _FakeSleep:
    """A SleepCycle double: triggers on the Energy signal and returns a report whose
    wake self-check passed (``ok=True``) or failed (``ok=False``) — no real pipeline."""

    def __init__(self, *, ok: bool) -> None:
        self._ok = ok

    @property
    def is_asleep(self) -> bool:
        return False

    def sleep_trigger(self, urges: Sequence[Urge], *, tick: int = 0) -> str | None:
        return "energy" if any(u.is_sleep_signal for u in urges) else None

    async def sleep(
        self, *, trigger: str, now: datetime | None = None, degraded_ticks: int = 0
    ) -> SleepReport:
        notes: dict[str, object] = (
            {} if self._ok else {"self_check_failures": ["anchor_consistency"]}
        )
        return SleepReport(
            trigger=trigger,
            started_at=_T0,
            ended_at=_T0,
            self_check_ok=self._ok,
            notes=notes,
        )


def _build_cycle(workspace: Workspace, spy: Any, sleep: Any) -> CognitiveCycle:
    """Build a cycle wired to the duck-typed deliberation spy + sleep double (``Any``
    so the structural doubles need no ``type: ignore`` at the call into the real ctor)."""
    return CognitiveCycle(workspace, deliberation=spy, sleep_cycle=sleep, sleep_fn=_noop_sleep)


async def test_a_failed_sleep_self_check_suspends_action_until_a_passing_check(
    workspace: Workspace,
) -> None:
    """After a sleep with ``self_check_ok=False`` the cycle is degraded — ``tick()``
    makes zero deliberate/act calls — and a later passing-check sleep restores it."""
    spy = _DeliberationSpy()
    cycle = _build_cycle(workspace, spy, _FakeSleep(ok=False))
    cycle._last_urges = _energy_urges()  # the trigger the run loop would have captured

    await cycle._maybe_sleep()
    assert cycle.has_full_agency is False  # failed wake self-check → degraded

    await cycle.tick()
    assert spy.deliberated == 0  # DELIBERATE suspended (no autonomous action)
    assert spy.acted == 0  # ACT suspended

    # A degraded alert was broadcast (surfaced on /ws/state + the REPL).
    alerts = await workspace.recent_events(limit=20, type_filter=_SLEEP_DEGRADED)
    assert alerts, "a sleep.degraded alert must be broadcast on a failed wake self-check"
    assert alerts[0].payload.get("full_agency") is False

    # A later sleep whose self-check passes restores full agency.
    cycle._sleep_cycle = _FakeSleep(ok=True)  # type: ignore[assignment]
    cycle._last_urges = _energy_urges()
    await cycle._maybe_sleep()
    assert cycle.has_full_agency is True

    await cycle.tick()
    assert spy.deliberated == 1  # resumed — autonomous action runs again


async def test_a_corrupted_boot_comes_up_degraded_and_takes_no_action(
    workspace: Workspace,
) -> None:
    """The restart path: a startup wake self-check that fails (a corrupted/tampered
    self-model loaded from disk) brings Johnny up degraded — the first tick makes no
    deliberate/act calls."""
    spy = _DeliberationSpy()
    cycle = _build_cycle(workspace, spy, None)  # no sleep cycle — this is the boot path

    await cycle.apply_wake_check(
        CheckResult(
            ok=False,
            findings=[
                CheckFinding(
                    check=CHECK_ANCHOR_CONSISTENCY, ok=False, detail="name diverged from anchor"
                )
            ],
        )
    )
    assert cycle.has_full_agency is False

    await cycle.tick()
    assert spy.deliberated == 0  # boots into degraded mode — no autonomous action

    alerts = await workspace.recent_events(limit=20, type_filter=_SLEEP_DEGRADED)
    assert alerts and alerts[0].payload.get("full_agency") is False


async def test_an_intact_boot_keeps_full_agency(workspace: Workspace) -> None:
    """A passing startup wake self-check leaves full agency intact — the first tick
    deliberates normally."""
    spy = _DeliberationSpy()
    cycle = _build_cycle(workspace, spy, None)

    await cycle.apply_wake_check(CheckResult(ok=True, findings=[]))
    assert cycle.has_full_agency is True

    await cycle.tick()
    assert spy.deliberated == 1  # full agency → deliberation runs
