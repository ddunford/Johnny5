"""Fixtures for the DB-backed LLM tests (the budget hard-gate).

Most of the ``tests/llm`` suite is pure (provider adapters, router resilience with
in-memory fakes) and needs nothing here. The budget gate, though, reads real daily
spend from ``llm_call_log`` via the Core ``BudgetGovernor``, so its test seeds that
table and runs **in-network** (``./ctl.sh test``). This fixture is opt-in — only a
test that requests ``clean_call_log`` touches the DB — so the pure tests are
unaffected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from sqlalchemy.ext.asyncio import AsyncEngine

_CALL_LOG_TABLES = ("llm_call_log",)


@pytest_asyncio.fixture
async def clean_call_log(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean ``llm_call_log`` on a loop-local engine (the budget governor reads it)."""
    engine = install_fresh_global_engine()
    await truncate_tables(_CALL_LOG_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(_CALL_LOG_TABLES)
        await dispose_global_engine()
