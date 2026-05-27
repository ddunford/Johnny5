"""Typed request/response envelopes for ``/api/v1``.

Each model is the **contract** the Phase-5b frontend service adapters pin against
(the canonical frontend↔backend failure mode — a hand-written TS interface is a
*claim*, these are the proof). Every response model is a projection of an existing
repository row / the live runtime; field names mirror the source exactly so a
server-side rename can't silently ship. Wire fixtures captured in TASK-5a.9 are
literal captures of these shapes (populated + empty-state).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# ── POST /input ────────────────────────────────────────────────────────────────


class InputRequest(BaseModel):
    """A message a human (the web UI) sends Johnny — enqueued as a percept.

    ``text`` must be non-blank (blank → 422). The handler enforces the max length
    (oversized → 413) and the queue-depth cap (full → 429); both bounds are
    settings, not hard-coded here.
    """

    text: str = Field(..., description="The message to send Johnny.")

    @field_validator("text")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class InputAccepted(BaseModel):
    """Acknowledges the message was enqueued (NOT a reply — the reply streams on
    ``/ws/consciousness`` a tick later, once Sensorium drains the queue)."""

    accepted: bool = True
    queue_depth: int = Field(..., description="Undrained inputs waiting after this push.")


# ── GET /state ───────────────────────────────────────────────────────────────
#
# StateSnapshot mirrors a ``/ws/state`` frame's PAYLOAD exactly: both are built by
# ``brain.cycle.serialize_state`` (FC-8 — REST snapshot for initial load, then the
# SPA switches to the live socket; one projection so they can't diverge). The
# handler validates the serializer's dict through these models, so a serializer
# change that breaks the shape fails loudly rather than silently shipping.


class DrivePayload(BaseModel):
    """One homeostatic drive's live pressure."""

    drive: str
    value: float
    setpoint: float
    threshold: float
    over_threshold: bool


class MoodPayload(BaseModel):
    """Johnny's mood — a point in valence×arousal space plus emotion tags."""

    valence: float
    arousal: float
    emotions: dict[str, float]
    descriptor: str
    mood_id: int | None = None


class GoalPayload(BaseModel):
    """The compact goal shape the state surface carries (``goals_to_payload``)."""

    id: int | None = None
    source: str
    description: str
    priority: float
    status: str
    plan: dict[str, object] = Field(default_factory=dict)


class SleepSummary(BaseModel):
    """A compact summary of the most recent completed sleep (``None`` before the first)."""

    trigger: str
    ended_at: str | None = None
    facts_written: int
    episodes_decayed: int
    facts_merged: int
    self_model_version: int | None = None
    self_check_ok: bool | None = None
    degraded_stages: list[str] = Field(default_factory=list)


class SleepBlock(BaseModel):
    """Sleep status on every snapshot — awake/asleep, the full-agency gate, last sleep."""

    asleep: bool
    full_agency: bool
    last: SleepSummary | None = None


class StateSnapshot(BaseModel):
    """The consolidated state snapshot — equal in shape to a ``/ws/state`` payload."""

    tick: int
    drives: list[DrivePayload] = Field(default_factory=list)
    mood: MoodPayload | None = None
    goals: list[GoalPayload] = Field(default_factory=list)
    interval: float
    sleep: SleepBlock


# ── GET /thoughts ────────────────────────────────────────────────────────────


class Thought(BaseModel):
    """One inner thought — the same shape ``/ws/consciousness`` streams."""

    id: int | None = None
    ts: str | None = None
    text: str


class ThoughtsResponse(BaseModel):
    """Recent thoughts, newest first (projection of ``workspace_event`` type=thought)."""

    thoughts: list[Thought] = Field(default_factory=list)


# ── GET /audit ─────────────────────────────────────────────────────────────────


class AuditEvent(BaseModel):
    """One persisted bus event (every broadcast is logged, FC-4/FC-5).

    Includes the FC-5 ``action.dispatched`` dispatch point — every action Johnny
    takes, internal or external, lands here.
    """

    id: int | None = None
    ts: str | None = None
    module: str
    type: str
    payload: dict[str, object] = Field(default_factory=dict)


class AuditResponse(BaseModel):
    """Recent bus events, newest first (projection of the ``workspace_event`` log)."""

    events: list[AuditEvent] = Field(default_factory=list)


# ── GET /memory/episodes ───────────────────────────────────────────────────────


class EpisodeOut(BaseModel):
    """One episodic memory (projection of an ``episode`` row).

    ``score`` is the blended recall relevance, populated only on the **search**
    path (``?q=``); it is ``null`` when browsing (no query).
    """

    id: int | None = None
    ts: str | None = None
    kind: str
    content: str
    actors: list[str] = Field(default_factory=list)
    emotion_tags: list[str] = Field(default_factory=list)
    salience: float
    score: float | None = None


class EpisodesResponse(BaseModel):
    """Episodic memories — recent (browse) or relevance-ranked (``?q=`` search)."""

    episodes: list[EpisodeOut] = Field(default_factory=list)


# ── GET /memory/facts ──────────────────────────────────────────────────────────


class FactOut(BaseModel):
    """One consolidated semantic fact (projection of a ``semantic_fact`` row).

    ``score`` is the cosine similarity, populated only on the **search** path
    (``?q=``); ``null`` when browsing recent facts.
    """

    id: int | None = None
    subject: str
    predicate: str
    object: str
    confidence: float
    source_episode_ids: list[int] = Field(default_factory=list)
    score: float | None = None


class FactsResponse(BaseModel):
    """Semantic facts — recent (browse) or similarity-ranked (``?q=`` search)."""

    facts: list[FactOut] = Field(default_factory=list)


# ── GET /goals ─────────────────────────────────────────────────────────────────


class GoalOut(BaseModel):
    """One goal (projection of a ``goal`` row) — the full lifecycle view.

    Richer than the compact ``GoalPayload`` on the state surface: it adds the
    ``outcome`` and the created/resolved timestamps for the goals panel.
    """

    id: int | None = None
    source: str
    description: str
    priority: float
    status: str
    plan: dict[str, object] = Field(default_factory=dict)
    outcome: dict[str, object] = Field(default_factory=dict)
    created_at: str | None = None
    resolved_at: str | None = None


class GoalsResponse(BaseModel):
    """Active goals (incumbent first) + recently closed goals (newest first)."""

    active: list[GoalOut] = Field(default_factory=list)
    recent: list[GoalOut] = Field(default_factory=list)


# ── GET /sleeps ────────────────────────────────────────────────────────────────


class SleepOut(BaseModel):
    """One completed/in-progress sleep (projection of a ``sleep_log`` row)."""

    id: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    trigger: str
    facts_written: int
    episodes_decayed: int
    facts_merged: int
    self_model_version: int | None = None
    snapshot_path: str | None = None
    self_check_ok: bool | None = None
    notes: dict[str, object] = Field(default_factory=dict)


class SleepsResponse(BaseModel):
    """Recent sleeps, newest first (projection of the ``sleep_log`` table)."""

    sleeps: list[SleepOut] = Field(default_factory=list)


# ── GET /self ──────────────────────────────────────────────────────────────────


class IdentityOut(BaseModel):
    """The current self-model version (projection of the latest ``identity`` row)."""

    name: str
    version: int
    self_model_doc: str
    values: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)


class SelfNote(BaseModel):
    """One self-improvement note (Metacognition's review output; ``status`` always
    ``open`` this phase — *applying* a proposal is the Phase-9 gated self-edit flow)."""

    ts: str | None = None
    observation: str
    proposal: str
    status: str


class SelfResponse(BaseModel):
    """Johnny's self-model + his recent reflections (read-only here, FC-1/FC-9)."""

    identity: IdentityOut | None = None
    notes: list[SelfNote] = Field(default_factory=list)
