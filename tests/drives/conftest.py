"""Fixtures for the drives + affect suite (the motivational core, Phase 3).

DB-backed: the drive engine persists each drive to ``drive_state``, the affect
model writes ``mood`` history (and ``thought.mood_id`` now points at it), and the
goal arbiter persists promotions to ``goal`` so an in-flight pursuit survives a
restart (TC-3.5). Like the cognition suite these run **in-network** via
``./ctl.sh test`` — host pytest can't reach the compose ``postgres``/``redis``
hosts (lessons.md). The cross-loop-safe engine + flushed-Redis plumbing is shared
from ``helpers.db`` and the top-level conftest; this module only supplies the
clean-schema fixture for the tables a drive/affect/goal test touches.

A drive test that closes the autonomy loop runs the *real* cognitive cycle, so it
writes to the heartbeat + memory tables too (RECALL reads episodes/facts, LEARN
writes an episode, the narrator writes ``thought``). The truncation set is
therefore the Phase-3 tables **plus** the heartbeat/memory tables, so every test
starts from a clean slate everywhere — not just an empty drive table.

Truncated children-first; ``CASCADE`` covers the FK edges (``thought.mood_id`` →
``mood``) and ``RESTART IDENTITY`` resets ids so order/identity assertions are
deterministic.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.workspace import Workspace

# Phase-3 motivational tables (children-first) followed by the heartbeat + memory
# tables a full autonomy-loop test also writes through. CASCADE + RESTART IDENTITY
# leaves every named table empty with ids reset.
_DRIVE_TABLES = (
    # Phase 3 — drives / affect / goals
    "goal",
    "mood",
    "drive_state",
    # Phase 2 — heartbeat (a cycle tick writes these; thought.mood_id → mood)
    "thought",
    "percept",
    "workspace_event",
    # Phase 1 — memory spine (RECALL reads, LEARN writes)
    "semantic_edge",
    "semantic_fact",
    "skill",
    "episode",
)


@pytest_asyncio.fixture
async def drives_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean drives/affect/goal schema on a fresh, loop-local global engine.

    The drive engine, affect model, and goal store all persist through
    ``session_scope()`` (the process-global engine), so installing a loop-local
    engine here is what lets them run under test. Tables start and end empty.
    """
    engine = install_fresh_global_engine()
    await truncate_tables(_DRIVE_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(_DRIVE_TABLES)
        await dispose_global_engine()


@pytest_asyncio.fixture
async def workspace(
    drives_db: AsyncEngine, redis_client: Redis, frozen_clock: FrozenClock
) -> Workspace:
    """A :class:`Workspace` on the flushed test Redis + clean Postgres, stamping
    events from the shared ``frozen_clock`` — for cycle-integration tests (the
    APPRAISE stage broadcasts drive/mood state, FC-8). Namespaced per test so
    parallel runs never collide on the bus or the blackboard."""
    suffix = uuid.uuid4().hex
    return Workspace(
        redis=redis_client,
        channel=f"johnny:test:{suffix}:bus",
        contents_key=f"johnny:test:{suffix}:contents",
        now_fn=datetime_from(frozen_clock),
    )
