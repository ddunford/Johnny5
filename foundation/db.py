"""Database foundation: the declarative base and (from the session task) the
async engine, session factory, and repository base.

A single ``Base`` carries a stable constraint-naming convention so Alembic can
autogenerate and later alter constraints deterministically. All persistent
models inherit from it; repository access goes through the session helpers.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

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
