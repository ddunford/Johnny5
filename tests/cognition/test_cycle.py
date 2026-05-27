"""TC-2.6 + TC-2.1 — the heartbeat is resilient and deterministic.

The cycle's hard invariant (``SPEC §7``): it **never dies**. A failed stage — most
importantly a "tired" LLM provider in the Narrator — degrades *that stage* and is
recorded on the tick, while the heartbeat continues and recovers when the
provider returns. These drive the real ``CognitiveCycle`` through the frozen-clock
harness with provider-free doubles (one of which raises on demand), so the
resilience is exercised without any real network or wall-clock.

DB+Redis backed (the workspace persists every broadcast) → run in-network via
``./ctl.sh test``.
"""

from __future__ import annotations

from collections.abc import Sequence

from helpers.clock import FrozenClock
from helpers.cycle import (
    CycleHarness,
    PassthroughAttention,
    RecordingLearning,
    StubNarration,
    StubPerception,
    build_cycle,
)

from brain.workspace import Workspace, WorkspaceItem


def _percepts() -> list[WorkspaceItem]:
    return [
        WorkspaceItem(kind="input", content="Dan said hello", salience=0.9),
        WorkspaceItem(kind="ambient", content="it is 09:00 UTC", salience=0.1),
    ]


class _ExplodingRecall:
    """A recall stage that always raises — to prove one broken stage doesn't take
    down the tick or the stages after it."""

    def __init__(self) -> None:
        self.calls = 0

    async def recall(self, *, focus: Sequence[WorkspaceItem]) -> Sequence[WorkspaceItem]:
        self.calls += 1
        raise RuntimeError("recall store unreachable")


async def test_heartbeat_survives_a_tired_narrator_and_recovers(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """The narrator's provider is 'tired' for the first two ticks (raises), then
    returns. The loop must keep ticking, degrade only the narrate stage, and
    resume narrating automatically — TC-2.6."""
    narration = StubNarration(fail_until=2)
    learning = RecordingLearning()
    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(_percepts()),
        attention=PassthroughAttention(),
        narration=narration,
        learning=learning,
    )

    reports = await harness.run_ticks(4)

    # The loop never died: all four ticks ran and returned reports.
    assert [r.tick for r in reports] == [1, 2, 3, 4]

    # Ticks 1–2: narrate degraded, but the heartbeat continued and the *other*
    # stages still ran (percepts perceived, a salient set selected).
    for degraded in reports[:2]:
        assert not degraded.ok
        assert "narrate" in degraded.stage_errors
        assert degraded.thought is None
        assert degraded.percept_count == 2
        assert degraded.content_count >= 1

    # Ticks 3–4: the provider recovered → clean ticks with a thought again.
    for healthy in reports[2:]:
        assert healthy.ok
        assert healthy.stage_errors == {}
        assert healthy.thought is not None

    # The narrator was actually attempted every tick (not skipped).
    assert narration.calls == 4
    # Learn ran every tick regardless of the narrate failure (it got thought=None
    # on the degraded ticks) — downstream stages are not skipped by an upstream fail.
    assert len(learning.learned) == 4
    assert learning.learned[0][1] is None  # degraded tick → no thought
    assert learning.learned[3][1] is not None  # recovered tick → thought present


async def test_one_broken_stage_does_not_stop_the_stages_after_it(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """A raising RECALL stage is isolated: narrate still runs and the tick survives,
    with only 'recall' marked degraded."""
    recall = _ExplodingRecall()
    narration = StubNarration()
    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(_percepts()),
        attention=PassthroughAttention(),
        recall=recall,
        narration=narration,
    )

    report = (await harness.run_ticks(1))[0]

    assert recall.calls == 1
    assert "recall" in report.stage_errors
    assert not report.ok
    # narrate ran despite recall blowing up → the thought is still produced.
    assert report.thought is not None
    assert narration.calls == 1


async def test_degraded_ticks_are_reproducible(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """Same inputs + frozen clock → identical degrade-then-recover signature across
    two independent runs (TC-2.1 determinism, including the failure path)."""

    def fresh_harness() -> CycleHarness:
        return build_cycle(
            workspace,
            clock=frozen_clock,
            perception=StubPerception(_percepts()),
            attention=PassthroughAttention(),
            narration=StubNarration(fail_until=1),
        )

    first = await fresh_harness().run_ticks(3)
    second = await fresh_harness().run_ticks(3)

    assert [(r.ok, r.thought) for r in first] == [(r.ok, r.thought) for r in second]
    assert [sorted(r.stage_errors) for r in first] == [sorted(r.stage_errors) for r in second]
