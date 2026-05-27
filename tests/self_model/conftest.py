"""Fixtures for the self-model suite (the evolving self-concept, Phase 4).

DB-backed, in-network (``./ctl.sh test``). ``SelfModel.refresh()`` reads the Core
identity anchor (read-only, FC-1), recent episodes + semantic facts, and drive/mood
history, then persists a **new** append-versioned ``identity`` row (latest =
current). The truncation set is therefore ``identity`` plus the memory + drive/mood
tables a refresh reads (``helpers.phase4.SELF_MODEL_TABLES``).

``identity`` is seeded v1 from the Core anchor by migration 0005; ``RESTART IDENTITY
CASCADE`` wipes that seed. The store re-establishes it idempotently:
``IdentityStore.ensure_seeded()`` (and ``SelfModel.bootstrap()`` / ``current()`` /
``refresh()``, which all call it) re-seed the anchor-grounded v1 if the table is
empty — the ``DriveEngine.bootstrap`` pattern. So a test needs no manual re-seed:
the first ``current()``/``refresh()`` self-seeds v1, then ``refresh()`` appends v2.
Mirrors the ``drives_db`` pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from helpers.phase4 import SELF_MODEL_TABLES
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest_asyncio.fixture
async def self_model_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean self-model schema (identity + read tables) on a fresh, loop-local
    global engine. The SelfModel store persists through ``session_scope()``, so the
    loop-local engine installed here is what lets it run under test."""
    engine = install_fresh_global_engine()
    await truncate_tables(SELF_MODEL_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(SELF_MODEL_TABLES)
        await dispose_global_engine()
