"""Alembic environment — async, driven by application settings.

The database URL comes from `foundation.config` (never hard-coded in
`alembic.ini`), and `target_metadata` is `Base.metadata` with every model module
imported so autogenerate sees the full schema.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import model modules so their tables register on Base.metadata. Add new model
# modules here as subsystems land (memory stores, etc.).
import brain.affect.agent  # noqa: F401
import brain.agents.narrator  # noqa: F401
import brain.agents.sensorium  # noqa: F401
import brain.drives.engine  # noqa: F401
import brain.llm.call_log  # noqa: F401
import brain.memory.episodic  # noqa: F401
import brain.memory.procedural  # noqa: F401
import brain.memory.semantic  # noqa: F401
import brain.workspace  # noqa: F401
from alembic import context
from foundation.config import get_settings
from foundation.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the live DSN from settings (overrides any placeholder in alembic.ini).
config.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit migration SQL using only the configured URL (no DBAPI required)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations against a live connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
