"""DB fixtures for the memory-spine tests — cross-loop safe by construction.

``foundation.db`` exposes a *process-global* async engine (correct for the one
uvicorn loop in production). Reusing it across the per-function event loops
pytest-asyncio creates is the #1 gotcha carried from Phase 0: a connection (or
engine) bound to test A's loop blows up inside test B's loop. So these fixtures
deliberately do **not** touch the global engine — each test gets a fresh,
short-lived engine with ``NullPool`` (no connection is retained past the test),
disposed in teardown.

Schema is applied once per session by running ``alembic upgrade head`` against
whatever database the settings point at — which ``./ctl.sh test`` sets to
``johnny5_test``. A guard refuses to migrate anything that isn't a ``*_test``
database, so this can never run against dev.

Two session styles:
  * ``db_session`` — rolled back in teardown. Repo writes ``flush`` rather than
    ``commit``, and recall queries run in the same transaction, so nothing is
    persisted between tests: perfect isolation for repository/recall unit tests.
  * ``clean_db`` — truncates the memory tables before and after the test, for
    the restart-persistence test (TASK-1.9) which must *commit* and then prove
    data survives a brand-new engine/connection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from foundation.config import get_settings

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The four memory tables (SPEC §12 / phase-1 plan). Truncated in dependency
# order via CASCADE so the persistence test starts from a known-empty state.
_MEMORY_TABLES = ("semantic_edge", "semantic_fact", "skill", "episode")


@pytest.fixture(scope="session")
def _migrated_test_db() -> None:
    """Bring the test database to ``head`` exactly once per test session.

    Imported lazily so a non-DB test run (e.g. ``tests/helpers``) never pays the
    alembic import cost, and so the safety guard runs before any migration.
    """
    settings = get_settings()
    if "test" not in settings.postgres_db.lower():
        pytest.fail(
            f"refusing to run memory DB tests against non-test database "
            f"{settings.postgres_db!r} — run via ./ctl.sh test (johnny5_test)"
        )

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def db_engine(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A fresh per-test async engine bound to the current event loop.

    ``NullPool`` means no connection outlives the test, which is what keeps this
    safe across pytest-asyncio's per-function loops.
    """
    engine = create_async_engine(
        get_settings().sqlalchemy_url, poolclass=NullPool, future=True
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose transaction is rolled back after the test (isolation)."""
    maker = async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)
    async with maker() as session:
        try:
            yield session
        finally:
            await session.rollback()


async def _truncate_memory_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        for table_name in _MEMORY_TABLES:
            await conn.execute(
                text(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE")
            )


@pytest_asyncio.fixture
async def clean_db(db_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Yield an engine over freshly-truncated memory tables; clean again after.

    For the restart-persistence test: it commits, disposes the engine, opens a
    new one, and asserts the data is still there — so the data must really land
    on disk, and the table must start and end empty.
    """
    await _truncate_memory_tables(db_engine)
    try:
        yield db_engine
    finally:
        await _truncate_memory_tables(db_engine)
