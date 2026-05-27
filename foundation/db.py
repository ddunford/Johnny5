"""Database foundation: declarative base, async engine/session, repository base.

A single ``Base`` carries a stable constraint-naming convention so Alembic can
autogenerate and later alter constraints deterministically. The engine and
session factory are process-wide (the cognitive loop runs headless, so DB access
must work outside any HTTP request); routes use the ``get_session`` dependency,
headless code uses ``session_scope()``. All access goes through repositories.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import MetaData, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from foundation.config import get_settings

# Deterministic constraint names so migrations can reference them across edits.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use.

    ``pool_pre_ping`` guards against stale connections after the DB restarts —
    important for a process that runs continuously for days.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().sqlalchemy_url,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """Dispose the engine and reset the factory (called on shutdown / in tests)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session for headless code: commit on success, roll back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session (caller controls commit)."""
    async with get_sessionmaker()() as session:
        yield session


async def ping() -> bool:
    """Return True if a trivial query round-trips to Postgres."""
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


class Repository[ModelT: Base]:
    """Base repository: a thin, typed wrapper over a session for one model.

    Subsystems (memory stores, call-log writer) subclass this and set ``model``,
    adding domain queries. Keeps all persistence behind an interface so it stays
    the most-tested seam.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, instance: ModelT) -> ModelT:
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def get(self, ident: Any) -> ModelT | None:
        return await self.session.get(self.model, ident)

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        result = await self.session.execute(select(self.model).limit(limit).offset(offset))
        return result.scalars().all()
