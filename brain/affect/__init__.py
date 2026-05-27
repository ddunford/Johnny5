"""Affect — Johnny's mood, appraised from his situation (``SPEC §6.2``).

An appraisal (goal-congruence, novelty, agency, certainty) projects into a
continuous valence×arousal mood plus discrete emotion tags. Mood persists, decays
toward calm, and colours cognition — tone, attention bias, and cycle rate.
"""

from __future__ import annotations

from brain.affect.agent import Affect, MoodRepository, MoodRow, derive_appraisal
from brain.affect.appraisal import (
    Appraisal,
    Mood,
    MoodDelta,
    appraise_dimensions,
    blend_mood,
    parse_appraisal,
    parse_mood_delta,
)

__all__ = [
    "Affect",
    "Appraisal",
    "Mood",
    "MoodDelta",
    "MoodRepository",
    "MoodRow",
    "appraise_dimensions",
    "blend_mood",
    "derive_appraisal",
    "parse_appraisal",
    "parse_mood_delta",
]
