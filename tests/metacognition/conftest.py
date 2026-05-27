"""Fixtures for the metacognition suite (offline self-review, Phase 4).

DB-backed, in-network (``./ctl.sh test``). ``Metacognition.review()`` inspects
recent outcomes — goals resolved vs. abandoned, degraded ticks, drive/mood
patterns — and writes zero-or-more ``self_improvement_note`` rows with
``status='open'`` (proposals only; applying them is the Phase-9 gated self-edit
flow — NOT here). The truncation set is ``self_improvement_note`` plus the outcome
tables a review reads (``helpers.phase4.METACOGNITION_TABLES``).

The defining assertion for this suite (TC-4.5) is the *negative* one: a review
proposes but never **applies** — nothing in prompts/drives/agents/config is mutated.
Mirrors the ``drives_db`` clean-schema pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from helpers.phase4 import METACOGNITION_TABLES
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest_asyncio.fixture
async def metacognition_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean metacognition schema (self_improvement_note + outcome read tables)
    on a fresh, loop-local global engine. The store persists through
    ``session_scope()``, so the engine installed here is what lets it run under
    test. Tables start and end empty for deterministic id/order assertions."""
    engine = install_fresh_global_engine()
    await truncate_tables(METACOGNITION_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(METACOGNITION_TABLES)
        await dispose_global_engine()
