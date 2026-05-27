"""Fixtures for the sleep suite (the offline growth pipeline, Phase 4).

DB-backed and in-network (``./ctl.sh test`` — host pytest can't reach the compose
``postgres``/``redis`` hosts, lessons.md). A sleep run is the broadest Phase-4
write surface: it consolidates episodes into semantic facts, decays/merges memory,
refreshes + versions the ``identity`` doc, writes ``self_improvement_note`` rows,
appends a ``sleep_log`` row, snapshots, restores Energy (writes ``drive_state``),
and emits ``persistence_confirmed`` — and it pauses a real heartbeat that itself
writes ``thought``/``percept``/``workspace_event``. So the truncation set is the
full Phase-0→4 surface (``helpers.phase4.SLEEP_TABLES``); every sleep test starts
from a clean slate everywhere, not just an empty ``sleep_log``.

The cross-loop-safe engine + flushed-Redis plumbing is shared from ``helpers.db``
and the top-level conftest; this module only supplies the clean-schema fixture and
a clock-stamped :class:`Workspace` (the sleep pipeline emits progress on the bus,
FC-8). Mirrors the ``drives_db`` pattern (tests/drives/conftest.py).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from helpers.phase4 import SLEEP_TABLES
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.workspace import Workspace


@pytest_asyncio.fixture
async def sleep_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean Phase-0→4 schema on a fresh, loop-local global engine.

    The sleep pipeline and every subsystem it drives persist through
    ``session_scope()`` (the process-global engine), so installing a loop-local
    engine here is what lets them run under test. Tables start and end empty.
    """
    engine = install_fresh_global_engine()
    await truncate_tables(SLEEP_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(SLEEP_TABLES)
        await dispose_global_engine()


@pytest_asyncio.fixture
async def workspace(
    sleep_db: AsyncEngine, redis_client: Redis, frozen_clock: FrozenClock
) -> Workspace:
    """A :class:`Workspace` on the flushed test Redis + clean Postgres, stamping
    events from the shared ``frozen_clock`` — for the sleep/wake state surfacing
    (FC-8: asleep/awake, last-sleep summary, self-model version emit on the bus).
    Namespaced per test so parallel runs never collide on the bus/blackboard."""
    suffix = uuid.uuid4().hex
    return Workspace(
        redis=redis_client,
        channel=f"johnny:test:{suffix}:bus",
        contents_key=f"johnny:test:{suffix}:contents",
        now_fn=datetime_from(frozen_clock),
    )
