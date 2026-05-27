"""Guards the memory-test DB harness itself (conftest fixtures).

Not a feature test — it pins the two infrastructure guarantees the whole memory
suite relies on, so a regression here is diagnosed as "harness broke" rather
than mis-blamed on a store:

  * the test DB is migrated and a ``db_session`` round-trips a real query;
  * the pgvector extension (memory embeddings depend on it) is installed;
  * two separate async tests each get a *fresh* engine on their own event loop
    without tripping the cross-loop global-engine gotcha (Phase 0 → Phase 1).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_session_round_trips(db_session: AsyncSession) -> None:
    assert (await db_session.execute(text("SELECT 1"))).scalar_one() == 1


async def test_pgvector_extension_installed(db_session: AsyncSession) -> None:
    installed = (
        await db_session.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
    ).scalar_one_or_none()
    assert installed == 1, "pgvector extension missing — recall embeddings cannot work"


async def test_fresh_engine_again_no_cross_loop_error(db_session: AsyncSession) -> None:
    # A second async test using the same fixtures must work on its own loop with
    # its own engine; if the harness leaked a global engine this would raise
    # "attached to a different loop".
    assert (await db_session.execute(text("SELECT 2"))).scalar_one() == 2
