"""Cross-loop-safe Postgres + Redis test plumbing, shared across DB-backed suites.

Carried from Phase 1 (the memory spine): the stores persist through
``foundation.db.session_scope()``, which uses a **process-global** async engine
(correct for production's single uvicorn loop). pytest-asyncio creates a fresh
event loop per test, and an engine bound to one loop blows up under another
("attached to a different loop") — the #1 gotcha of Phase 0/1. The fix, proven
there, is to install a **fresh NullPool engine bound to each test's own loop**
and dispose it in teardown.

These primitives are deliberately **table-agnostic** so every DB-backed suite
reuses the same loop-safety machinery and only supplies its own table list:
the memory spine truncates its tables, the cognition suite truncates the
workspace tables. DB/Redis-backed tests reach Postgres/Redis at the compose
hostnames, which only resolve **in-network** — run them via ``./ctl.sh test``,
not host pytest (lessons.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import foundation.db as db
from foundation.config import Settings, get_settings

# tests/helpers/db.py -> parents[2] is the project root (alongside alembic.ini).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def migrate_test_db_to_head() -> None:
    """Bring the test database to ``head`` exactly once; refuse a non-test DB.

    Intended to back a session-scoped fixture so the schema is applied a single
    time per run. The guard mirrors the Phase-1 safety check: never migrate a
    database whose name doesn't contain ``test``.
    """
    settings = get_settings()
    if "test" not in settings.postgres_db.lower():
        pytest.fail(
            f"refusing to migrate non-test database {settings.postgres_db!r} "
            "— run via ./ctl.sh test (johnny5_test)"
        )

    from alembic.config import Config

    from alembic import command

    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")


def install_fresh_global_engine() -> AsyncEngine:
    """Point ``foundation.db``'s process-global engine at a new NullPool engine
    bound to the current event loop (mirrors what ``dispose_engine`` manages).

    Because the stores read the global engine via ``session_scope()``, installing
    a loop-local engine here is what lets them run under test at all.
    """
    engine = create_async_engine(get_settings().sqlalchemy_url, poolclass=NullPool, future=True)
    db._engine = engine
    db._sessionmaker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return engine


async def dispose_global_engine() -> None:
    """Dispose the process-global engine (teardown counterpart of the installer)."""
    await db.dispose_engine()


async def restart_global_engine() -> AsyncEngine:
    """Drop all engine/connection state and reconnect fresh — the in-process
    stand-in for ``./ctl.sh down && up``. A follow-up read therefore comes from
    disk via brand-new connections, not cached session/engine state."""
    await db.dispose_engine()
    return install_fresh_global_engine()


async def truncate_tables(tables: Sequence[str]) -> None:
    """``TRUNCATE ... RESTART IDENTITY CASCADE`` the given tables in order.

    Leaves every named table empty with ids reset, so order/identity assertions
    are deterministic. The caller supplies an FK-safe order (CASCADE covers
    edges, but listing children first keeps intent clear).
    """
    async with db.get_engine().begin() as conn:
        for table_name in tables:
            await conn.execute(text(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE"))


def require_testing_env() -> Settings:
    """Return settings, refusing to proceed with destructive setup outside testing.

    Backs the Redis-flush fixture: ``./ctl.sh test`` sets ``APP_ENV=testing`` and
    points ``REDIS_URL`` at db 1, so a guarded ``flushdb`` only ever touches the
    isolated test database.
    """
    settings = get_settings()
    if settings.app_env.lower() != "testing":
        pytest.fail(
            f"refusing to run destructive test setup outside the testing env "
            f"(app_env={settings.app_env!r}) — run via ./ctl.sh test"
        )
    return settings
