"""Async SQLAlchemy engine/session factory.

Connection string comes from the DATABASE_URL environment variable,
falling back to DEFAULT_DATABASE_URL below.

PG mode (current): local PostgreSQL 16 portable at 127.0.0.1:55432
(see deploy/README-pg.md for start/stop commands).
SQLite downgrade mode: set DATABASE_URL=sqlite+aiosqlite:///./medops.db
(dialect differences are isolated to the connection string).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://medops:medops@127.0.0.1:55432/medops"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or get_database_url())


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


engine = make_engine()
async_session_factory = make_session_factory(engine)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a scoped async session."""
    async with async_session_factory() as session:
        yield session
