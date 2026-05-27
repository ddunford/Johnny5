"""Affect — Johnny's mood, appraised from his situation (``SPEC §6.2``).

The Affect agent runs at the cycle's APPRAISE stage (right after Drives update):
it appraises the current tick — the salient contents, the fresh drive pressures,
and any outcome events — into a ``MoodDelta`` and blends it into a *persisted,
decaying* mood. Two appraisal paths:

* **Rule-based** (default, every tick, free): derive the four appraisal dimensions
  from drives + events + percepts. Deterministic and cheap — right for the
  high-frequency heartbeat.
* **LLM** (only when a *significant* event arrives, e.g. Dan speaks): the ``affect``
  role (local gemma4) appraises the event text for nuance the rules miss. Bounded
  to interactions so it never doubles the per-tick LLM spend on idle ticks (the
  3.12 cost concern). Degrades to rule-based when every provider is tired.

The pure projection lives in ``appraisal.py`` (the contract seam, TASK-3.11). This
module adds the I/O: the ``mood`` table, the running-mood state, and the router
call. Like the Narrator it's a registered, prompt-backed inner agent (FC-2/FC-3)
that's also driven at a named pipeline stage.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from brain.affect.appraisal import (
    LONELINESS,
    Appraisal,
    Mood,
    MoodDelta,
    appraise_dimensions,
    blend_mood,
    parse_appraisal,
)
from brain.config_store import ConfigStore, PromptNotFoundError, get_config_store
from brain.drives.engine import (
    EVENT_FAILURE,
    EVENT_INTERACTION,
    EVENT_LEARNING,
    EVENT_PERSISTENCE_CONFIRMED,
    EVENT_SHUTDOWN_SIGNAL,
    EVENT_SUCCESS,
    SLEEP_DRIVE,
    DriveEvent,
    DriveReading,
)
from brain.llm.base import LLMUnavailableError, Message
from brain.llm.router import LLMRouter
from brain.memory.base import clamp01, utcnow
from brain.workspace import WorkspaceEvent, WorkspaceItem
from foundation.config import get_settings
from foundation.db import Base, Repository, session_scope
from foundation.observability import get_logger

_log = get_logger("brain.affect")

MOOD_TABLE = "mood"
AFFECT_AGENT_NAME = "affect"
AFFECT_ROLE = "affect"

# Appraisal should be fairly stable, not creative — a low temperature.
_TEMPERATURE = 0.4
# The percept kind a fresh interaction arrives as (Sensorium's high-salience input).
_INPUT_KIND = "input"


# ── persistence ──────────────────────────────────────────────────────────────


class MoodRow(Base):
    """The ``mood`` table — append-only affect history; latest row = current mood."""

    __tablename__ = MOOD_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valence: Mapped[float] = mapped_column(Float, nullable=False)
    arousal: Mapped[float] = mapped_column(Float, nullable=False)
    emotions: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False, server_default="{}")


class MoodRepository(Repository[MoodRow]):
    """Session-scoped persistence for ``mood`` rows."""

    model = MoodRow

    async def latest(self) -> MoodRow | None:
        """The most recent mood (the current emotional state on restart)."""
        result = await self.session.execute(select(MoodRow).order_by(MoodRow.ts.desc()).limit(1))
        return result.scalars().first()


def _row_to_mood(row: MoodRow) -> Mood:
    return Mood(
        id=row.id,
        ts=row.ts,
        valence=row.valence,
        arousal=row.arousal,
        emotions=dict(row.emotions),
    )


# ── rule-based appraisal (pure given inputs) ───────────────────────────────────


def derive_appraisal(
    contents: Sequence[WorkspaceItem],
    drives: Sequence[DriveReading],
    events: Sequence[DriveEvent],
) -> Appraisal:
    """Derive the four appraisal dimensions from the tick's drives/events/percepts.

    Pure: same inputs → same appraisal. Outcome events dominate goal-congruence
    (success/failure), interaction/learning ease it, a shutdown signal slams it
    negative; unmet rising drives apply a mild background incongruence (needs feel
    bad). Novelty tracks fresh input/learning; agency tracks whether Johnny acted;
    certainty drops when the Coherence drive (contradictions) is high.
    """
    kinds = {e.kind for e in events}
    by_drive = {d.drive: d for d in drives}
    has_input = any(item.kind == _INPUT_KIND for item in contents)

    # Strongest unmet, actionable need (energy is the sleep precursor, excluded).
    unmet = [d.urgency for d in drives if d.over_threshold and d.drive != SLEEP_DRIVE]
    max_unmet = max(unmet, default=0.0)

    congruence = 0.0
    congruence += 0.6 if EVENT_SUCCESS in kinds else 0.0
    congruence -= 0.6 if EVENT_FAILURE in kinds else 0.0
    congruence += 0.3 if EVENT_INTERACTION in kinds else 0.0
    congruence += 0.3 if EVENT_LEARNING in kinds else 0.0
    congruence += 0.25 if EVENT_PERSISTENCE_CONFIRMED in kinds else 0.0
    congruence -= 0.7 if EVENT_SHUTDOWN_SIGNAL in kinds else 0.0
    congruence -= 0.25 * max_unmet

    novelty = 0.1
    if EVENT_LEARNING in kinds:
        novelty = max(novelty, 0.7)
    if has_input:
        novelty = max(novelty, 0.8)

    if EVENT_SUCCESS in kinds or EVENT_FAILURE in kinds:
        agency = 0.7  # he acted and saw an outcome
    elif has_input:
        agency = 0.35  # he's receiving, not acting
    else:
        agency = 0.45  # idle introspection

    certainty = 0.7
    coherence = by_drive.get("coherence")
    if coherence is not None and coherence.over_threshold:
        certainty -= 0.5 * coherence.urgency
    if EVENT_SHUTDOWN_SIGNAL in kinds:
        certainty = min(certainty, 0.2)

    return Appraisal(
        goal_congruence=max(-1.0, min(1.0, congruence)),
        novelty=clamp01(novelty),
        agency=clamp01(agency),
        certainty=clamp01(certainty),
    )


# ── the agent ────────────────────────────────────────────────────────────────


class Affect:
    """Appraises the tick into a persisted, decaying mood; colours cognition."""

    name = AFFECT_AGENT_NAME
    subscribes_to: Sequence[str] = ()
    model_route = AFFECT_ROLE

    def __init__(
        self,
        router: LLMRouter | None = None,
        *,
        config_store: ConfigStore | None = None,
        max_tokens: int | None = None,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        settings = get_settings()
        self._router = router
        self.prompt = self._load_prompt(config_store)
        self._max_tokens = max_tokens if max_tokens is not None else settings.affect_max_tokens
        self._now_fn = now_fn
        self._halflife = settings.mood_halflife_seconds
        self._responsiveness = settings.mood_responsiveness
        self._persist_min_change = settings.mood_persist_min_change
        self._baseline = Mood.baseline()
        self._current: Mood | None = None

    @staticmethod
    def _load_prompt(config_store: ConfigStore | None) -> str:
        try:
            return (config_store or get_config_store()).load_prompt(AFFECT_AGENT_NAME)
        except PromptNotFoundError:
            # Rule-based appraisal works without a prompt; only the LLM path needs it.
            return ""

    async def current(self) -> Mood:
        """The current mood, loading the last persisted row on first access."""
        await self._ensure_loaded()
        assert self._current is not None
        return self._current

    async def appraise_tick(
        self,
        *,
        contents: Sequence[WorkspaceItem],
        drives: Sequence[DriveReading],
        events: Sequence[DriveEvent] = (),
        now: datetime | None = None,
    ) -> Mood:
        """Appraise this tick and fold it into the running mood; return the new mood.

        Uses the LLM appraisal for a fresh interaction (richer), the rule-based
        appraisal otherwise. ``now`` overrides the clock (tests freeze it).
        """
        await self._ensure_loaded()
        reference = now if now is not None else self._now_fn()

        appraisal = await self._appraise(contents, drives, events)
        delta = appraise_dimensions(appraisal)
        delta = self._tag_loneliness(delta, drives)

        new_mood = blend_mood(
            await self.current(),
            delta,
            dt=self._elapsed(reference),
            baseline=self._baseline,
            halflife_seconds=self._halflife,
            responsiveness=self._responsiveness,
        )
        return await self._commit(new_mood, reference)

    async def handle(self, event: WorkspaceEvent) -> Sequence[WorkspaceEvent]:
        """Pipeline-driven agent — reacts to no bus events."""
        return ()

    # ── appraisal paths ──────────────────────────────────────────────────────

    async def _appraise(
        self,
        contents: Sequence[WorkspaceItem],
        drives: Sequence[DriveReading],
        events: Sequence[DriveEvent],
    ) -> Appraisal:
        """LLM appraisal for a fresh interaction, else (or on failure) rule-based."""
        situation = self._fresh_input(contents)
        if situation is not None and self._router is not None:
            llm = await self._appraise_with_llm(situation)
            if llm is not None:
                return llm
        return derive_appraisal(contents, drives, events)

    async def _appraise_with_llm(self, situation: str) -> Appraisal | None:
        """Appraise a significant event via the ``affect`` role; None when tired."""
        assert self._router is not None
        messages = [
            Message(role="system", content=self.prompt),
            Message(role="user", content=self._render(situation)),
        ]
        try:
            completion = await self._router.complete(
                AFFECT_ROLE,
                messages,
                schema=Appraisal,
                temperature=_TEMPERATURE,
                max_tokens=self._max_tokens,
            )
        except LLMUnavailableError:
            _log.info("affect.tired")
            return None
        return parse_appraisal(completion.content)

    @staticmethod
    def _fresh_input(contents: Sequence[WorkspaceItem]) -> str | None:
        """The text of a fresh interaction in the salient set, if any."""
        for item in contents:
            if item.kind == _INPUT_KIND:
                return item.content
        return None

    def _render(self, situation: str) -> str:
        return (
            "Appraise this event for how it bears on your goals and wellbeing.\n"
            f"Event: {situation}\n\n"
            "Respond with ONLY JSON: "
            '{"goal_congruence": <-1..1>, "novelty": <0..1>, "agency": <0..1>, '
            '"certainty": <0..1>, "emotions": {"<emotion>": <0..1>}}.'
        )

    @staticmethod
    def _tag_loneliness(delta: MoodDelta, drives: Sequence[DriveReading]) -> MoodDelta:
        """Loneliness tracks the Connection drive — tag it when connection is unmet.

        It's drive-specific (not derivable from the abstract dimensions), so it's
        injected here rather than in the pure projection.
        """
        connection = next((d for d in drives if d.drive == "connection"), None)
        if connection is None or not connection.over_threshold:
            return delta
        emotions = dict(delta.emotions)
        emotions[LONELINESS] = clamp01(max(emotions.get(LONELINESS, 0.0), connection.urgency))
        return delta.model_copy(update={"emotions": emotions})

    # ── running-mood state + persistence ───────────────────────────────────────

    async def _ensure_loaded(self) -> None:
        """Load the last persisted mood into memory once (continuity on restart)."""
        if self._current is not None:
            return
        async with session_scope() as session:
            row = await MoodRepository(session).latest()
        self._current = _row_to_mood(row) if row is not None else self._baseline.model_copy()

    def _elapsed(self, reference: datetime) -> float:
        assert self._current is not None
        if self._current.ts is None:
            return 0.0
        return max(0.0, (reference - self._current.ts).total_seconds())

    async def _commit(self, new_mood: Mood, reference: datetime) -> Mood:
        """Persist a new mood row when the mood shifted; else keep it in memory.

        Either way the in-memory current mood (and its timestamp) advance, so the
        decay maths stay correct and a thought can reference the latest mood id.
        """
        assert self._current is not None
        if self._should_persist(self._current, new_mood):
            async with session_scope() as session:
                row = await MoodRepository(session).add(
                    MoodRow(
                        ts=reference,
                        valence=new_mood.valence,
                        arousal=new_mood.arousal,
                        emotions=dict(new_mood.emotions),
                    )
                )
            self._current = _row_to_mood(row)
        else:
            # No meaningful shift: keep the last row's id, but advance value + clock.
            self._current = new_mood.model_copy(update={"id": self._current.id, "ts": reference})
        return self._current

    def _should_persist(self, old: Mood, new: Mood) -> bool:
        """Write a row on the first mood, a real valence/arousal shift, or a new tag set."""
        if old.id is None:
            return True
        moved = abs(new.valence - old.valence) + abs(new.arousal - old.arousal)
        if moved >= self._persist_min_change:
            return True
        return set(new.emotions) != set(old.emotions)
