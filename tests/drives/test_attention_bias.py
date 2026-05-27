"""Mood + drives bias Attention (TC-3.4 attention half, SPEC §6.2).

Affect must *steer* cognition, not decorate it. The cycle hands Attention an
``AttentionBias`` each tick (``set_bias``) built from the current mood + unmet
drives; Attention folds it into its salience score:

* **Arousal sharpens focus** — an activated Johnny amplifies salient items and
  damps marginal ones (he narrows in), measured relative to the calm baseline
  (arousal 0.3).
* **Unmet-drive kind pulls** — a drive over threshold boosts the percept kinds
  that help satisfy it (Connection → fresh ``input``, Curiosity → recalled
  ``memory``/``fact``), so what Johnny *needs* shapes what he attends to.

Tests the REAL ``Attention`` agent directly (it makes no LLM/DB call — salience
selection is the deterministic LIDA mechanism), so these run host-side. Assertions
are on selection/score *effects*, robust to the final salience clamp.
"""

from __future__ import annotations

from brain.agents.attention import Attention, AttentionBias
from brain.workspace import WorkspaceItem


async def test_unmet_drive_kind_boost_changes_what_is_attended() -> None:
    """A drive-relevant kind boost lifts that kind's score enough to overtake a
    higher-salience item — so an unmet drive redirects the bounded focus."""
    fact = WorkspaceItem(kind="fact", content="the sky is blue", salience=0.50)
    fresh_input = WorkspaceItem(kind="input", content="Dan said hello", salience=0.45)

    # Unbiased, the higher-salience fact wins the single slot.
    unbiased = Attention(capacity=1)
    picked = await unbiased.select(working_memory=[fact], percepts=[fresh_input])
    assert [i.content for i in picked] == ["the sky is blue"]

    # An unmet Connection drive boosts ``input`` — now the interaction wins.
    biased = Attention(capacity=1)
    biased.set_bias(AttentionBias(arousal=0.3, kind_boosts={"input": 0.5}))
    picked = await biased.select(working_memory=[fact], percepts=[fresh_input])
    assert [i.content for i in picked] == ["Dan said hello"]


async def test_high_arousal_damps_marginal_items() -> None:
    """High arousal narrows focus: a below-mid-salience item scores lower than at
    the calm baseline (the marginal stuff dims when Johnny is activated)."""
    marginal = WorkspaceItem(kind="ambient", content="host load is 0.2", salience=0.20)

    calm = Attention(capacity=5)
    calm_salience = (await calm.select(working_memory=[marginal], percepts=[]))[0].salience

    aroused = Attention(capacity=5)
    aroused.set_bias(AttentionBias(arousal=0.9))
    aroused_salience = (await aroused.select(working_memory=[marginal], percepts=[]))[0].salience

    assert aroused_salience < calm_salience


async def test_neutral_bias_leaves_selection_unbiased() -> None:
    """The default (calm, no boosts) bias is a no-op — Phase-2 behaviour preserved,
    so a bare Attention and the test doubles are unaffected."""
    item = WorkspaceItem(kind="memory", content="a quiet thought", salience=0.6)

    default_bias = Attention(capacity=5)
    explicit_neutral = Attention(capacity=5)
    explicit_neutral.set_bias(AttentionBias())

    a = (await default_bias.select(working_memory=[item], percepts=[]))[0].salience
    b = (await explicit_neutral.select(working_memory=[item], percepts=[]))[0].salience
    assert a == b
