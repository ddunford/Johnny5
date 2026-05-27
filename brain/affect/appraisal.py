"""Appraisal → mood — the pure core of Johnny's affect (``SPEC §6.2``).

An **appraisal** evaluates a situation along four cognitive dimensions —
**goal-congruence, novelty, agency, certainty** (the appraisal-theory axes) — and
projects them into a continuous **mood** (``valence`` × ``arousal``) plus discrete
**emotion** tags. Everything here is pure (no I/O): the stateful agent in
``agent.py`` reads drives/contents, calls these functions, and persists the
result. Keeping the projection pure is the house-rule contract seam — ``SPEC §6.2``
emotions can't silently drift because a fixture of the model's appraisal output is
fed through ``parse_mood_delta`` in the contract test (TASK-3.11).

Mood is *stateful and decaying*: an appraisal nudges it, and between nudges it
relaxes toward a calm baseline (a feeling fades). ``blend_mood`` is that update —
pure, given the elapsed time, so it's freezable in tests.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from brain.memory.base import clamp01

# The discrete emotions Johnny tags (``SPEC §6.2``). Loneliness is drive-specific
# (it tracks the Connection drive) so it's tagged by the agent, not derivable from
# the four abstract dimensions alone — the others fall out of the appraisal.
JOY = "joy"
EXCITEMENT = "excitement"
CONTENTMENT = "contentment"
FRUSTRATION = "frustration"
ANXIETY = "anxiety"
LONELINESS = "loneliness"

# A faint emotion isn't worth tagging — drop anything below this intensity.
_EMOTION_FLOOR = 0.12


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


# ── domain types ─────────────────────────────────────────────────────────────


class Appraisal(BaseModel):
    """The four appraisal dimensions evaluated for a situation.

    ``goal_congruence`` ∈ [-1, 1] (does it help or thwart Johnny's goals/drives);
    ``novelty``/``agency``/``certainty`` ∈ [0, 1]. Optional ``emotions`` lets an
    LLM appraisal name what it felt directly; when absent they're derived.
    """

    goal_congruence: float = 0.0
    novelty: float = 0.0
    agency: float = 0.5
    certainty: float = 0.5
    emotions: dict[str, float] = Field(default_factory=dict)


class MoodDelta(BaseModel):
    """An appraisal's push on mood: signed valence/arousal nudges + emotion tags.

    ``valence``/``arousal`` are pushes in [-1, 1] (the direction/strength to move
    current mood, before it's scaled and blended). ``emotions`` are intensities in
    [0, 1] to fold into the running emotional colour.
    """

    valence: float = 0.0
    arousal: float = 0.0
    emotions: dict[str, float] = Field(default_factory=dict)


class Mood(BaseModel):
    """Johnny's emotional state — a point in valence×arousal space + emotion tags.

    ``valence`` ∈ [-1, 1] (pleasant↔unpleasant); ``arousal`` ∈ [0, 1]
    (calm↔activated). ``id``/``ts`` are set once persisted.
    """

    id: int | None = None
    ts: datetime | None = None
    valence: float = 0.0
    arousal: float = 0.3
    emotions: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def baseline(cls) -> Mood:
        """The calm-neutral resting mood feelings decay toward."""
        return cls(valence=0.0, arousal=0.3, emotions={})

    @property
    def dominant_emotion(self) -> str | None:
        """The strongest tagged emotion, if any (drives narration tone + UI)."""
        if not self.emotions:
            return None
        return max(self.emotions, key=lambda e: self.emotions[e])

    def descriptor(self) -> str:
        """A short tone phrase for the narrator prompt (Phase 3.4 wiring).

        Derived from the valence×arousal quadrant, sharpened by the dominant
        emotion when one is tagged — so the inner voice reads as how he feels.
        """
        if self.valence >= 0.15:
            quadrant = "bright and energised" if self.arousal >= 0.5 else "calm and content"
        elif self.valence <= -0.15:
            quadrant = "tense and on edge" if self.arousal >= 0.5 else "low and flat"
        else:
            quadrant = "alert and neutral" if self.arousal >= 0.5 else "quiet and even"
        dominant = self.dominant_emotion
        return f"{quadrant}, with a thread of {dominant}" if dominant else quadrant


# ── the projection: appraisal → mood delta (pure) ──────────────────────────────


def appraise_dimensions(appraisal: Appraisal) -> MoodDelta:
    """Project the four appraisal dimensions into a mood delta + emotion tags.

    Valence follows goal-congruence (helped = pleasant). Arousal rises with
    novelty, uncertainty, and the stakes (|congruence|) and falls toward calm when
    nothing's happening. Emotions fall out of the congruence×arousal quadrant,
    modulated by agency (a *blocked* goal you had control over reads as
    frustration; an uncertain threat reads as anxiety). An LLM appraisal may
    override the derived emotions by naming its own.
    """
    congruence = _clamp(appraisal.goal_congruence, -1.0, 1.0)
    novelty = clamp01(appraisal.novelty)
    agency = clamp01(appraisal.agency)
    certainty = clamp01(appraisal.certainty)

    valence = congruence
    # Centred at 0: a dull, certain, low-stakes tick pushes arousal *down* (calm);
    # novelty / uncertainty / high stakes push it up.
    arousal = _clamp(
        0.55 * novelty + 0.35 * (1.0 - certainty) + 0.30 * abs(congruence) - 0.45,
        -1.0,
        1.0,
    )

    emotions = (
        dict(appraisal.emotions)
        if appraisal.emotions
        else _derive_emotions(
            congruence=congruence, novelty=novelty, agency=agency, certainty=certainty
        )
    )
    emotions = {e: clamp01(v) for e, v in emotions.items() if clamp01(v) >= _EMOTION_FLOOR}
    return MoodDelta(valence=valence, arousal=arousal, emotions=emotions)


def _derive_emotions(
    *, congruence: float, novelty: float, agency: float, certainty: float
) -> dict[str, float]:
    """Tag discrete emotions from the appraisal quadrant (``SPEC §6.2``)."""
    emotions: dict[str, float] = {}
    if congruence > 0:
        # Pleasant: aroused → excitement/joy, calm → contentment.
        emotions[EXCITEMENT] = congruence * novelty
        emotions[JOY] = congruence * agency
        emotions[CONTENTMENT] = congruence * (1.0 - novelty) * certainty
    elif congruence < 0:
        # Unpleasant: blocked-with-control → frustration; uncertain → anxiety.
        emotions[FRUSTRATION] = -congruence * agency
        emotions[ANXIETY] = -congruence * (1.0 - certainty)
    return emotions


def parse_appraisal(content: str) -> Appraisal:
    """Project an LLM appraisal response (JSON) into an ``Appraisal`` (pure)."""
    return Appraisal.model_validate_json(content)


def parse_mood_delta(content: str) -> MoodDelta:
    """LLM appraisal JSON → ``MoodDelta`` — the full contract projection (TASK-3.11)."""
    return appraise_dimensions(parse_appraisal(content))


# ── the stateful update: blend a delta into the running mood (pure) ────────────


def blend_mood(
    current: Mood,
    delta: MoodDelta,
    *,
    dt: float,
    baseline: Mood,
    halflife_seconds: float,
    responsiveness: float = 0.5,
    emotion_decay: float = 0.6,
) -> Mood:
    """Decay ``current`` toward ``baseline`` over ``dt``, then apply ``delta`` (pure).

    A mood fades: deviation from baseline halves every ``halflife_seconds`` (so an
    excited spike mellows if nothing sustains it). The appraisal then nudges
    valence/arousal a ``responsiveness`` fraction and folds its emotion tags into
    the (decayed) running set. ``id``/``ts`` are intentionally dropped — the caller
    stamps them on persist.
    """
    retained = 0.5 ** (max(0.0, dt) / halflife_seconds) if halflife_seconds > 0 else 0.0

    valence = baseline.valence + (current.valence - baseline.valence) * retained
    arousal = baseline.arousal + (current.arousal - baseline.arousal) * retained
    valence = _clamp(valence + delta.valence * responsiveness, -1.0, 1.0)
    arousal = clamp01(arousal + delta.arousal * responsiveness)

    emotions: dict[str, float] = {
        e: v * retained * emotion_decay for e, v in current.emotions.items()
    }
    for emotion, intensity in delta.emotions.items():
        emotions[emotion] = clamp01(emotions.get(emotion, 0.0) + intensity * responsiveness)
    emotions = {e: v for e, v in emotions.items() if v >= _EMOTION_FLOOR}

    return Mood(valence=valence, arousal=arousal, emotions=emotions)
