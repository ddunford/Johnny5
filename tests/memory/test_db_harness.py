"""Guards the memory-test DB harness itself (conftest fixtures).

Not a feature test — it pins the infrastructure guarantees the whole memory
suite relies on, so a regression here is diagnosed as "harness broke" rather
than mis-blamed on a store:

  * the test DB is migrated and ``session_scope()`` round-trips a real query on
    the loop-local global engine ``memory_db`` installs;
  * the pgvector extension (memory embeddings depend on it) is installed;
  * a second async test gets a *fresh* engine on its own loop without the
    cross-loop global-engine error (the Phase 0 → Phase 1 gotcha).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from foundation.db import session_scope


async def test_session_round_trips(memory_db: AsyncEngine) -> None:
    async with session_scope() as session:
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1


async def test_pgvector_extension_installed(memory_db: AsyncEngine) -> None:
    async with session_scope() as session:
        installed = (
            await session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        ).scalar_one_or_none()
    assert installed == 1, "pgvector extension missing — recall embeddings cannot work"


async def test_fresh_engine_again_no_cross_loop_error(memory_db: AsyncEngine) -> None:
    # A second async test reinstalling the global engine on its own loop must
    # work; if the harness leaked an engine across loops this would raise
    # "attached to a different loop".
    async with session_scope() as session:
        assert (await session.execute(text("SELECT 2"))).scalar_one() == 2
