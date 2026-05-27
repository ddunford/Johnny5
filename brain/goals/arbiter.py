"""The goal arbiter — promote the winning urge to a goal (``SPEC §6.1`` / §6.3).

When drives cross threshold they emit urges; the arbiter turns the strongest into
a **goal** Johnny pursues. Two design rules carry the autonomy loop:

* **Priority = intensity × affect.** A goal's priority is the urge's urgency
  scaled by arousal — an activated Johnny pursues his strongest need more
  decisively.
* **Hysteresis (anti-thrash).** A just-promoted goal is held for a dwell window
  before *any* competitor can displace it, and after the dwell a challenger from a
  *different* drive must beat the incumbent's current priority by a margin. So
  Johnny commits to a pursuit instead of flip-flopping between curiosity and
  connection every tick (TC-3.6). The dwell keys off the goal's persisted
  ``created_at``, so the anti-thrash behaviour survives a restart.

``select`` is the pure decision (testable without a DB); ``arbitrate`` wraps it
with the store — loading the incumbent, abandoning it on a switch, and persisting
the promotion. The Energy "sleep" urge is excluded here: it isn't a goal to
pursue, it's a request to sleep (Phase 4).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from brain.affect.appraisal import Mood
from brain.drives.engine import Urge
from brain.goals.store import Goal, GoalStore
from brain.memory.base import utcnow
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.goals.arbiter")

# How each drive's unmet pressure reads as an internal intent (the goal text).
# The concrete internal *action* (reflect/recall/…) is Deliberation's job (3.6);
# this is the motivation, in Johnny's own first-person framing.
_DRIVE_INTENT: dict[str, str] = {
    "curiosity": "explore something new and learn",
    "boredom": "find something fresh to engage with",
    "connection": "reconnect — reach out or reflect on the people I care about",
    "mastery": "practise and get better at something I've struggled with",
    "coherence": "reconcile something that doesn't add up in what I believe",
    "continuity": "make sure I'll persist — check my memory is safe",
}


def _describe(drive: str) -> str:
    return _DRIVE_INTENT.get(drive, f"address my rising {drive}")


class GoalArbiter:
    """Promotes urges to goals with affect-weighted priority + anti-thrash hysteresis."""

    def __init__(
        self,
        *,
        store: GoalStore | None = None,
        min_dwell_seconds: float | None = None,
        hysteresis_margin: float | None = None,
        arousal_weight: float | None = None,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        settings = get_settings()
        self._store = store or GoalStore(now_fn=now_fn)
        self._min_dwell = (
            min_dwell_seconds if min_dwell_seconds is not None else settings.goal_min_dwell_seconds
        )
        self._margin = (
            hysteresis_margin if hysteresis_margin is not None else settings.goal_hysteresis_margin
        )
        self._arousal_weight = (
            arousal_weight if arousal_weight is not None else settings.goal_arousal_weight
        )
        self._now_fn = now_fn

    def priority(self, urge: Urge, mood: Mood | None) -> float:
        """Affect-weighted priority for an urge (intensity × affect, ``SPEC §6.1``)."""
        arousal = mood.arousal if mood is not None else 0.0
        return urge.urgency * (1.0 + self._arousal_weight * arousal)

    def select(
        self,
        urges: Sequence[Urge],
        mood: Mood | None,
        active_goals: Sequence[Goal],
        *,
        now: datetime | None = None,
    ) -> Goal | None:
        """Decide the goal to *promote*, or ``None`` to keep the incumbent (pure).

        ``None`` means "no change" — either keep pursuing the incumbent or, when
        there's nothing actionable, stay idle. A returned ``Goal`` is the new
        pursuit the caller should persist (displacing the incumbent).
        """
        reference = now if now is not None else self._now_fn()
        actionable = [u for u in urges if not u.is_sleep_signal]
        if not actionable:
            return None

        best = max(actionable, key=lambda u: self.priority(u, mood))
        incumbent = active_goals[0] if active_goals else None

        if incumbent is None:
            return self._goal_from_urge(best, mood)

        # Hysteresis 1 — minimum dwell: don't switch a fresh pursuit at all.
        if incumbent.created_at is not None:
            held = (reference - incumbent.created_at).total_seconds()
            if held < self._min_dwell:
                return None

        # Same drive still winning → keep pursuing it (no churn).
        if best.drive == incumbent.source:
            return None

        # Hysteresis 2 — margin: a different drive must clearly beat the incumbent's
        # *current* priority (0 if its drive is no longer over threshold).
        incumbent_priority = self._incumbent_priority(incumbent, urges, mood)
        if self.priority(best, mood) > incumbent_priority + self._margin:
            return self._goal_from_urge(best, mood)
        return None

    async def arbitrate(
        self, urges: Sequence[Urge], mood: Mood | None, *, now: datetime | None = None
    ) -> Goal | None:
        """Load the incumbent, decide, and persist a promotion. Returns the pursuit.

        The return is the goal Johnny is now pursuing (a freshly promoted one, the
        retained incumbent, or ``None`` when idle) — Deliberation acts on it.
        """
        active = await self._store.active()
        decision = self.select(urges, mood, active, now=now)
        if decision is None:
            return active[0] if active else None

        # Switching: the displaced incumbent is abandoned (Johnny moved on) so a
        # single active pursuit is maintained.
        if active:
            await self._store.abandon(
                active[0].id,  # type: ignore[arg-type]  # active rows always have an id
                outcome={"abandoned_for": decision.source},
            )
        promoted = await self._store.promote(decision)
        _log.info(
            "goal.promoted",
            goal_id=promoted.id,
            source=promoted.source,
            priority=round(promoted.priority, 4),
        )
        return promoted

    def _goal_from_urge(self, urge: Urge, mood: Mood | None) -> Goal:
        return Goal(
            source=urge.drive,
            description=_describe(urge.drive),
            priority=self.priority(urge, mood),
            created_at=self._now_fn(),
        )

    def _incumbent_priority(
        self, incumbent: Goal, urges: Sequence[Urge], mood: Mood | None
    ) -> float:
        """The incumbent drive's current urge priority (0 if it's fallen below threshold)."""
        for urge in urges:
            if urge.drive == incumbent.source:
                return self.priority(urge, mood)
        return 0.0
