"""DB fixtures for the memory-spine tests — cross-loop safe by construction.

The cross-loop-safe engine plumbing (a fresh NullPool engine bound to each
test's own loop, the migrate guard, the Redis flush guard, the restart
primitive) lives in ``helpers.db`` and is shared with the cognition suite. The
shared ``_migrated_test_db`` / ``redis_client`` / ``simulate_restart`` fixtures
live in the top-level ``conftest.py``. This module only wires the memory tables.

``memory_db`` gives each test a **fresh** loop-local global engine over an empty
memory schema: because the stores read the global engine via ``session_scope()``,
installing it here is what lets them run under test. Tables start and end empty,
so order/identity assertions are deterministic. Schema is applied once per
session by ``_migrated_test_db`` (refuses any non-``*_test`` database), which
``./ctl.sh test`` points at ``johnny5_test``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from sqlalchemy.ext.asyncio import AsyncEngine

# Truncated in FK-safe order (edges reference facts); CASCADE + RESTART IDENTITY
# leaves every memory table empty with ids reset for deterministic assertions.
_MEMORY_TABLES = ("semantic_edge", "semantic_fact", "skill", "episode")


@pytest_asyncio.fixture
async def memory_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean memory schema on a fresh, loop-local global engine.

    Tests drive the stores (which call ``session_scope()``) and may read back via
    ``session_scope()`` too — both resolve to the engine installed here.
    """
    engine = install_fresh_global_engine()
    await truncate_tables(_MEMORY_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(_MEMORY_TABLES)
        await dispose_global_engine()
