"""Affect contract: appraisal → MoodDelta projection (TASK-3.11, SPEC §6.2).

The house rule: every adapter that parses a model response is pinned by a literal
captured envelope, so a model output-shape change surfaces *here*, not silently in
production cognition. Affect has two appraisal paths and both are covered:

1. **LLM path** — the ``affect`` role (gemma4) returns the four appraisal
   dimensions as JSON. Pinned against REAL captured gemma4 envelopes
   (``ollama_gemma4_affect_*.json``): the two-layer projection
   ``parse_chat_completion`` → ``parse_appraisal`` → ``appraise_dimensions`` must
   take the clean ``content`` JSON and never let gemma4's ~2000-char reasoning
   preamble leak into the structured mood. An empty/garbage appraisal must fail
   loudly so the router fails over (it degrades to the rule-based path) rather than
   emit a garbage mood.
2. **Rule-based path** — ``derive_appraisal`` maps the tick's drives/events into the
   same four dimensions deterministically (failure→negative+frustration,
   interaction→valence lift, unmet drives→background incongruence). This is the
   default every tick; it's the oracle for "same inputs → same appraisal".

Pure (no I/O, no DB, no LLM) — feeds captured envelopes / hand-built appraisals
straight through the pure projection in ``brain/affect/appraisal.py``. The live
end-to-end leg (real model, real token budget) is the separate
``@pytest.mark.live`` guard in ``test_affect_live.py``.

KEY FINDING captured here: with the appraisal persona + ``json_object``, gemma4:e4b
emits a long ``reasoning`` channel before the JSON — the ``content``-first
projection is exactly why a too-small ``affect_max_tokens`` (the 512 default)
silently kills the LLM path. The empty-content test below documents that trap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from brain.affect.agent import Affect, derive_appraisal
from brain.affect.appraisal import (
    ANXIETY,
    CONTENTMENT,
    EXCITEMENT,
    FRUSTRATION,
    JOY,
    LONELINESS,
    Appraisal,
    Mood,
    MoodDelta,
    appraise_dimensions,
    blend_mood,
    parse_appraisal,
    parse_mood_delta,
)
from brain.drives.engine import (
    EVENT_FAILURE,
    EVENT_INTERACTION,
    EVENT_SHUTDOWN_SIGNAL,
    EVENT_SUCCESS,
    DriveEvent,
    DriveReading,
)
from brain.llm.providers.openai_compatible import parse_chat_completion

pytestmark = pytest.mark.contract

FixtureLoader = Callable[[str], Any]

# Real captured gemma4 affect envelopes (system = config/prompts/affect.md, the
# agent's exact request at affect_max_tokens=1024). Both carry a reasoning channel.
POSITIVE_FIXTURE = "llm/ollama_gemma4_affect_positive_interaction.json"
FRUSTRATING_FIXTURE = "llm/ollama_gemma4_affect_frustrating_failure.json"

# The discrete-emotion vocabulary Affect tags (SPEC §6.2) — nothing else may appear.
AFFECT_EMOTIONS = frozenset({JOY, EXCITEMENT, CONTENTMENT, FRUSTRATION, ANXIETY, LONELINESS})


def _content_of(envelope: dict[str, Any]) -> str:
    return envelope["choices"][0]["message"]["content"]


# ── LLM path: the two-layer projection on REAL gemma4 envelopes ──────────────


@pytest.mark.parametrize("fixture_name", [POSITIVE_FIXTURE, FRUSTRATING_FIXTURE])
def test_real_gemma4_envelope_projects_without_reasoning_leakage(
    load_fixture: FixtureLoader, fixture_name: str
) -> None:
    """The full path on a captured gemma4 envelope that carries BOTH a long
    reasoning chain and the JSON content: content-first at the provider, then the
    appraisal — and the reasoning never contaminates the structured mood."""
    envelope = load_fixture(fixture_name)
    message = envelope["choices"][0]["message"]
    reasoning = message["reasoning"]
    assert reasoning, "fixture must carry a reasoning channel for this to mean anything"
    assert len(reasoning) > len(message["content"])  # reasoning is the bulk

    # Layer 1: the provider projects content-first (reasoning kept separate).
    completion = parse_chat_completion(envelope, provider="ollama", requested_model="gemma4:e4b")
    assert completion.content == message["content"]  # the JSON, NOT the reasoning
    assert completion.reasoning == reasoning
    assert completion.content != completion.reasoning

    # Layer 2: the appraisal + mood delta parse from the clean JSON content only.
    appraisal = parse_appraisal(completion.content)
    delta = parse_mood_delta(completion.content)

    # The reasoning's prose never leaked into the structured projection.
    assert "Thinking" not in completion.content
    assert "thinking process" not in completion.content.lower()
    # MoodDelta is bounded numbers + the known emotion vocabulary — no free text.
    assert -1.0 <= delta.valence <= 1.0
    assert -1.0 <= delta.arousal <= 1.0
    assert set(delta.emotions) <= AFFECT_EMOTIONS
    assert all(0.0 <= v <= 1.0 for v in delta.emotions.values())
    # And valence tracks the appraised congruence (the projection's core claim).
    assert (delta.valence > 0) == (appraisal.goal_congruence > 0)


def test_positive_interaction_projects_to_a_mild_pleasant_delta(
    load_fixture: FixtureLoader,
) -> None:
    """The real positive-event envelope → a small positive valence; the model's faint
    contentment (0.1) is below the emotion floor (0.12) so it's dropped, not tagged."""
    content = _content_of(load_fixture(POSITIVE_FIXTURE))
    appraisal = parse_appraisal(content)
    assert appraisal.goal_congruence == pytest.approx(0.1)
    assert appraisal.emotions == {"contentment": 0.1}  # what the model named

    delta = parse_mood_delta(content)
    assert delta.valence == pytest.approx(0.1)
    assert delta.arousal == pytest.approx(-0.08)  # dull/certain/low-stakes → calming
    assert delta.emotions == {}  # contentment 0.1 < 0.12 floor → dropped


def test_frustrating_failure_projects_to_a_negative_delta_with_named_emotions(
    load_fixture: FixtureLoader,
) -> None:
    """The real frustrating-event envelope → negative valence and the emotions the
    model NAMED (frustration, anxiety) pass straight through (not re-derived)."""
    content = _content_of(load_fixture(FRUSTRATING_FIXTURE))
    delta = parse_mood_delta(content)
    assert delta.valence == pytest.approx(-0.7)
    assert delta.arousal == pytest.approx(0.045)
    assert delta.emotions == pytest.approx({"frustration": 0.6, "anxiety": 0.3})
    assert set(delta.emotions) <= AFFECT_EMOTIONS


def test_empty_appraisal_content_fails_loudly_for_router_failover() -> None:
    """The token-budget trap: too small a ``max_tokens`` lets gemma4's reasoning
    preamble eat the whole budget, leaving ``content=""``. The parse MUST raise so
    the router treats it as a schema failure and fails over / degrades to the
    rule-based path — never fabricating a neutral mood from nothing."""
    with pytest.raises(ValueError):
        parse_appraisal("")
    with pytest.raises(ValueError):
        parse_mood_delta("")


def test_non_json_prose_is_rejected() -> None:
    """A model that ignores the JSON contract (returns prose) fails at the parse
    seam, not by smuggling a malformed appraisal into the mood."""
    with pytest.raises(ValueError):
        parse_appraisal("I feel uneasy about being switched off.")


# ── the pure dims → mood projection (appraise_dimensions) ────────────────────


def test_valence_follows_goal_congruence() -> None:
    assert appraise_dimensions(Appraisal(goal_congruence=0.8)).valence == pytest.approx(0.8)
    assert appraise_dimensions(Appraisal(goal_congruence=-0.5)).valence == pytest.approx(-0.5)


def test_novelty_and_uncertainty_raise_arousal_dullness_lowers_it() -> None:
    """A dull, certain, low-stakes tick is calming (arousal pushed down); novelty +
    uncertainty + stakes push it up."""
    calm = appraise_dimensions(Appraisal(goal_congruence=0.0, novelty=0.0, certainty=1.0))
    excited = appraise_dimensions(Appraisal(goal_congruence=0.0, novelty=1.0, certainty=0.0))
    assert calm.arousal < 0.0
    assert excited.arousal > calm.arousal


def test_emotions_derived_from_the_quadrant_when_the_model_names_none() -> None:
    """When the appraisal names no emotions, they fall out of the
    congruence×agency×certainty quadrant (SPEC §6.2)."""
    pleasant = appraise_dimensions(
        Appraisal(goal_congruence=0.8, novelty=0.7, agency=0.8, certainty=0.6)
    )
    assert pleasant.emotions
    assert set(pleasant.emotions) <= {JOY, EXCITEMENT, CONTENTMENT}

    # Negative + high agency reads as frustration (a goal you could control, blocked).
    blocked = appraise_dimensions(Appraisal(goal_congruence=-0.8, agency=0.9, certainty=0.8))
    assert blocked.emotions.get(FRUSTRATION, 0.0) > 0.0
    # Negative + low certainty reads as anxiety (an uncertain threat).
    threat = appraise_dimensions(Appraisal(goal_congruence=-0.8, agency=0.1, certainty=0.1))
    assert threat.emotions.get(ANXIETY, 0.0) > 0.0


def test_faint_emotions_below_the_floor_are_dropped() -> None:
    """Emotions below the intensity floor aren't worth tagging."""
    delta = appraise_dimensions(
        Appraisal(goal_congruence=0.1, novelty=0.1, agency=0.1, certainty=0.9)
    )
    assert delta.emotions == {}


def test_model_named_emotions_override_the_derivation() -> None:
    """An LLM appraisal that names its own emotions overrides the quadrant
    derivation — loneliness here, not the frustration the congruence would derive."""
    appraisal = Appraisal(goal_congruence=-0.5, agency=0.9, emotions={LONELINESS: 0.7})
    delta = appraise_dimensions(appraisal)
    assert delta.emotions == {LONELINESS: 0.7}


# ── the rule-based path (derive_appraisal) — deterministic oracle ────────────


def test_failure_event_appraises_negative_and_projects_to_frustration() -> None:
    appraisal = derive_appraisal(contents=[], drives=[], events=[DriveEvent(kind=EVENT_FAILURE)])
    assert appraisal.goal_congruence < 0.0
    delta = appraise_dimensions(appraisal)
    assert delta.valence < 0.0
    assert delta.emotions.get(FRUSTRATION, 0.0) > 0.0


def test_success_event_appraises_positive() -> None:
    appraisal = derive_appraisal(contents=[], drives=[], events=[DriveEvent(kind=EVENT_SUCCESS)])
    assert appraisal.goal_congruence > 0.0
    assert appraise_dimensions(appraisal).valence > 0.0


def test_interaction_event_lifts_valence() -> None:
    interaction = [DriveEvent(kind=EVENT_INTERACTION)]
    appraisal = derive_appraisal(contents=[], drives=[], events=interaction)
    assert appraisal.goal_congruence > 0.0


def test_shutdown_signal_slams_congruence_and_certainty_negative() -> None:
    appraisal = derive_appraisal(
        contents=[], drives=[], events=[DriveEvent(kind=EVENT_SHUTDOWN_SIGNAL)]
    )
    assert appraisal.goal_congruence < -0.5
    assert appraisal.certainty <= 0.2


def test_unmet_actionable_drives_apply_background_incongruence() -> None:
    """A rising unmet need (over-threshold curiosity) reads as mild negative
    congruence — needs feel bad — even with no events at all."""
    curiosity = DriveReading(
        drive="curiosity",
        value=0.80,
        setpoint=0.10,
        accrual_rate=0.0008,
        decay_rate=0.0002,
        threshold=0.65,
    )
    appraisal = derive_appraisal(contents=[], drives=[curiosity], events=[])
    assert appraisal.goal_congruence < 0.0


def test_loneliness_is_tagged_when_connection_is_unmet() -> None:
    """Loneliness is drive-specific (it tracks the Connection drive), so the agent
    injects it rather than deriving it from the abstract dimensions."""
    delta = MoodDelta(valence=-0.2, arousal=0.1, emotions={})
    unmet = DriveReading(
        drive="connection",
        value=0.85,
        setpoint=0.10,
        accrual_rate=0.0005,
        decay_rate=0.0001,
        threshold=0.70,
    )
    tagged = Affect._tag_loneliness(delta, [unmet])
    assert tagged.emotions.get(LONELINESS, 0.0) > 0.0


def test_loneliness_not_tagged_when_connection_is_met() -> None:
    delta = MoodDelta(valence=0.2, arousal=0.1, emotions={})
    met = DriveReading(
        drive="connection",
        value=0.30,
        setpoint=0.10,
        accrual_rate=0.0005,
        decay_rate=0.0001,
        threshold=0.70,
    )
    assert Affect._tag_loneliness(delta, [met]).emotions == {}


# ── the stateful blend (blend_mood) — pure, freezable ────────────────────────


def test_blend_mood_decays_toward_baseline_then_applies_the_delta() -> None:
    """One halflife of elapsed time halves the deviation from baseline, then the
    appraisal nudges valence/arousal a ``responsiveness`` fraction and folds its
    emotion tags into the decayed running set — exact, given dt."""
    baseline = Mood.baseline()  # valence 0.0, arousal 0.3
    prior = Mood(valence=0.8, arousal=0.9, emotions={EXCITEMENT: 0.6})
    delta = MoodDelta(valence=-0.4, arousal=-0.2, emotions={FRUSTRATION: 0.5})

    blended = blend_mood(
        prior,
        delta,
        dt=180.0,  # one halflife
        baseline=baseline,
        halflife_seconds=180.0,
        responsiveness=0.5,
        emotion_decay=0.6,
    )

    # valence: 0 + (0.8-0)*0.5 = 0.4 decayed; + (-0.4 * 0.5) = 0.2
    assert blended.valence == pytest.approx(0.2)
    # arousal: 0.3 + (0.9-0.3)*0.5 = 0.6 decayed; + (-0.2 * 0.5) = 0.5
    assert blended.arousal == pytest.approx(0.5)
    # excitement decays (0.6 × 0.5 retained × 0.6 emotion_decay = 0.18); frustration
    # folds in at responsiveness (0.5 × 0.5 = 0.25).
    assert blended.emotions[EXCITEMENT] == pytest.approx(0.18)
    assert blended.emotions[FRUSTRATION] == pytest.approx(0.25)
    # id/ts are dropped — the caller stamps them on persist.
    assert blended.id is None and blended.ts is None


def test_blend_mood_with_no_appraisal_relaxes_toward_calm() -> None:
    """With no delta, a feeling fades: the mood relaxes toward the calm baseline."""
    prior = Mood(valence=0.9, arousal=0.9, emotions={JOY: 0.5})
    blended = blend_mood(
        prior,
        MoodDelta(),
        dt=180.0,
        baseline=Mood.baseline(),
        halflife_seconds=180.0,
    )
    assert abs(blended.valence) < abs(prior.valence)
    assert blended.arousal < prior.arousal
