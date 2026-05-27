"""Validates the frozen-clock cycle harness itself (TASK-2.11 / TC-2.1).

These assert the *harness mechanics* the behavioural cycle tests rely on: it
steps the real ``CognitiveCycle`` deterministically and reproducibly, with no
wall-clock sleep and no LLM/network, while genuinely exercising the DB-backed
workspace (every broadcast is persisted) and the Redis blackboard. DB+Redis
backed, so run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from helpers.clock import FrozenClock
from helpers.cycle import (
    PassthroughAttention,
    RecordingLearning,
    StubNarration,
    StubPerception,
    build_cycle,
)

from brain.workspace import Workspace, WorkspaceItem


def _percepts() -> list[WorkspaceItem]:
    return [
        WorkspaceItem(kind="percept", content="the kettle is boiling", salience=0.8),
        WorkspaceItem(kind="percept", content="a fly is on the window", salience=0.2),
    ]


async def test_run_ticks_steps_the_cycle_n_times(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(_percepts()),
        attention=PassthroughAttention(capacity=7),
        narration=StubNarration(),
        learning=RecordingLearning(),
    )

    reports = await harness.run_ticks(5)

    # Exactly N ticks, numbered 1..N, each a clean pass (no stage degraded).
    assert [r.tick for r in reports] == [1, 2, 3, 4, 5]
    assert harness.cycle.tick_count == 5
    assert all(r.ok for r in reports)
    # Each tick perceived two percepts and narrated a thought from the contents.
    assert all(r.percept_count == 2 for r in reports)
    assert all(r.thought == "the kettle is boiling | a fly is on the window" for r in reports)


async def test_harness_is_reproducible(workspace: Workspace, frozen_clock: FrozenClock) -> None:
    """Two identically-configured harnesses produce the identical thought stream —
    the determinism the frozen clock + provider-free doubles guarantee (TC-2.1)."""
    first = await build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(_percepts()),
        attention=PassthroughAttention(),
        narration=StubNarration(),
    ).run_ticks(4)
    second = await build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(_percepts()),
        attention=PassthroughAttention(),
        narration=StubNarration(),
    ).run_ticks(4)

    assert [r.thought for r in first] == [r.thought for r in second]
    assert [r.content_count for r in first] == [r.content_count for r in second]


async def test_every_broadcast_is_persisted_and_blackboard_updates(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """The harness drives the real DB-backed workspace: ticks land on the bus log
    and the salient set is written to the Redis blackboard."""
    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(_percepts()),
        attention=PassthroughAttention(),
        narration=StubNarration(),
    )

    await harness.run_ticks(3)

    # The bus log persisted broadcasts (one cycle.tick marker per tick, at least).
    tick_markers = await workspace.recent_events(limit=100, type_filter="cycle.tick")
    assert len(tick_markers) == 3
    # The blackboard holds the (bounded) current salient set, most-salient first.
    contents = await workspace.contents()
    assert [item.content for item in contents] == [
        "the kettle is boiling",
        "a fly is on the window",
    ]
