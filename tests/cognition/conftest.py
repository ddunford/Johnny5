"""Fixtures for the cognition suite (heartbeat + Global Workspace).

These are DB **and** Redis backed: the workspace persists every broadcast to
``workspace_event`` (Postgres) and stores the salient blackboard in Redis, and
the cycle folds percepts into Redis working memory. So they run **in-network**
via ``./ctl.sh test`` — host pytest can't reach the compose ``postgres``/``redis``
hosts (lessons.md). The cross-loop-safe engine + flushed-Redis plumbing is shared
from ``helpers.db`` and the top-level conftest; this module wires the workspace
tables and hands out a ready-to-use :class:`Workspace` on a frozen clock.
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

# Every table a tick can touch: the Phase-2 heartbeat tables (percept/thought/
# workspace_event) plus the Phase-1 memory tables, because the cycle's RECALL
# stage reads episodes/facts and its LEARN stage *writes* an episode — so a
# cognition test must start from a clean memory slate too, not just an empty bus.
# Truncated children-first (edges → facts); CASCADE + RESTART IDENTITY leaves each
# empty with ids reset for deterministic assertions.
_HEARTBEAT_TABLES = (
    "semantic_edge",
    "semantic_fact",
    "skill",
    "episode",
    "percept",
    "thought",
    "workspace_event",
)


@pytest_asyncio.fixture
async def workspace_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean heartbeat schema on a fresh, loop-local global engine.

    The workspace + memory stores persist through ``session_scope()`` (the
    process-global engine), so installing a loop-local engine here is what lets
    ``broadcast`` and episodic writes run under test. Tables start and end empty.
    """
    engine = install_fresh_global_engine()
    await truncate_tables(_HEARTBEAT_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(_HEARTBEAT_TABLES)
        await dispose_global_engine()


@pytest_asyncio.fixture
async def workspace(
    workspace_db: AsyncEngine, redis_client: Redis, frozen_clock: FrozenClock
) -> Workspace:
    """A :class:`Workspace` on the flushed test Redis + clean Postgres, stamping
    events from the shared ``frozen_clock``.

    The pub/sub channel and contents key are namespaced per test so parallel runs
    never collide on the bus or the blackboard.
    """
    suffix = uuid.uuid4().hex
    return Workspace(
        redis=redis_client,
        channel=f"johnny:test:{suffix}:bus",
        contents_key=f"johnny:test:{suffix}:contents",
        now_fn=datetime_from(frozen_clock),
    )
