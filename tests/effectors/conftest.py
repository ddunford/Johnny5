"""Fixtures for the effectors suite (action_log audit + the no-secrets guard).

These tests exercise the **real** write paths — the Core's append-only
``AuditWriter`` (Postgres ``action_log``) and the Global Workspace's bus
(``workspace_event``) — so they are DB- (and, for the bus, Redis-) backed and run
**in-network** via ``./ctl.sh test`` (host pytest can't reach the compose
``postgres``/``redis`` hosts; lessons.md). The cross-loop-safe engine plumbing is
shared from ``helpers.db``; here we only name the tables a dispatch/broadcast can
touch and hand back a clean slate.
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

# The append-only trail + the bus log a dispatched/vetoed action writes to.
_EFFECTOR_TABLES = ("action_log", "workspace_event")


@pytest_asyncio.fixture
async def effector_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean ``action_log`` + ``workspace_event`` slate on a loop-local engine.

    The AuditWriter + Workspace persist through ``session_scope()`` (the process-
    global engine), so installing a loop-local engine here is what lets their
    writes run under test. Tables start and end empty for deterministic assertions.
    """
    engine = install_fresh_global_engine()
    await truncate_tables(_EFFECTOR_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(_EFFECTOR_TABLES)
        await dispose_global_engine()


@pytest_asyncio.fixture
async def bus(
    effector_db: AsyncEngine, redis_client: Redis, frozen_clock: FrozenClock
) -> Workspace:
    """A real :class:`Workspace` on the flushed test Redis + clean Postgres.

    Namespaced per test so parallel bus/blackboard state never collides; stamps
    events from the shared frozen clock for deterministic timestamps.
    """
    suffix = uuid.uuid4().hex
    return Workspace(
        redis=redis_client,
        channel=f"johnny:test:{suffix}:bus",
        contents_key=f"johnny:test:{suffix}:contents",
        now_fn=datetime_from(frozen_clock),
    )
