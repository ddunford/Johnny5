"""DB fixtures for the memory-spine tests — cross-loop safe by construction.

The memory stores persist through ``foundation.db.session_scope()``, which uses
a **process-global** async engine (correct for production's single uvicorn loop,
and the same pattern as ``DatabaseCallLogger``). Reusing that global engine
across the per-function event loops pytest-asyncio creates is the #1 gotcha
carried from Phase 0: an engine bound to test A's loop blows up inside test B's
loop ("attached to a different loop").

``memory_db`` defuses it by construction: each test gets a **fresh** global
engine, created with ``NullPool`` (no connection survives the test) and bound to
that test's own loop, then disposed on the same loop in teardown. Because the
stores read the global engine via ``session_scope()``, installing it here is
what lets them run under test at all.

Schema is applied once per session via ``alembic upgrade head`` against whatever
database the settings point at — which ``./ctl.sh test`` sets to ``johnny5_test``.
A guard refuses to migrate anything that isn't a ``*_test`` database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from redis.asyncio import Redis, from_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import foundation.db as db
from foundation.config import get_settings

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Truncated in FK-safe order (edges reference facts); CASCADE + RESTART IDENTITY
# leaves every memory table empty with ids reset for deterministic assertions.
_MEMORY_TABLES = ("semantic_edge", "semantic_fact", "skill", "episode")


@pytest.fixture(scope="session")
def _migrated_test_db() -> None:
    """Bring the test database to ``head`` exactly once per test session."""
    settings = get_settings()
    if "test" not in settings.postgres_db.lower():
        pytest.fail(
            f"refusing to run memory DB tests against non-test database "
            f"{settings.postgres_db!r} — run via ./ctl.sh test (johnny5_test)"
        )

    from alembic.config import Config

    from alembic import command

    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")


def _install_fresh_global_engine() -> AsyncEngine:
    """Point ``foundation.db``'s process-global engine at a new NullPool engine
    bound to the current event loop (mirrors what ``dispose_engine`` manages)."""
    engine = create_async_engine(get_settings().sqlalchemy_url, poolclass=NullPool, future=True)
    db._engine = engine
    db._sessionmaker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return engine


async def _truncate_memory_tables() -> None:
    async with db.get_engine().begin() as conn:
        for table_name in _MEMORY_TABLES:
            await conn.execute(text(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def memory_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean memory schema on a fresh, loop-local global engine.

    Tests drive the stores (which call ``session_scope()``) and may read back via
    ``session_scope()`` too — both resolve to the engine installed here. Tables
    start and end empty, so order/identity assertions are deterministic.
    """
    engine = _install_fresh_global_engine()
    await _truncate_memory_tables()
    try:
        yield engine
    finally:
        await _truncate_memory_tables()
        await db.dispose_engine()


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    """A fresh, loop-local Redis client over a flushed test DB (working memory).

    Like the engine fixture, a per-test client avoids reusing a connection bound
    to another event loop. ``./ctl.sh test`` points ``REDIS_URL`` at db 1; a
    guard refuses to flush unless running in the testing env, and ``flushdb``
    only affects the connected (test) database.
    """
    settings = get_settings()
    if settings.app_env.lower() != "testing":
        pytest.fail(
            f"refusing to flush Redis outside the testing env (app_env={settings.app_env!r}) "
            "— run via ./ctl.sh test"
        )
    client: Redis = from_url(settings.redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()
