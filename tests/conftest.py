"""Shared core-test fixtures: scratch database + async engine (PG or SQLite)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from medops_core.db import DEFAULT_DATABASE_URL, make_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

PG_AVAILABLE = os.environ.get("MEDOPS_TEST_DB_URL", DEFAULT_DATABASE_URL).startswith(
    "postgresql"
)


@pytest.fixture()
async def scratch_db() -> AsyncIterator[str]:
    """Yield a DATABASE_URL pointing at a fresh scratch database."""
    override = os.environ.get("MEDOPS_TEST_DB_URL")
    if override and override.startswith("sqlite"):
        url = override.replace("<<<scratch>>>", uuid.uuid4().hex)
        yield url
        return

    if not PG_AVAILABLE:
        pytest.skip("PG not available and MEDOPS_TEST_DB_URL not set to sqlite")

    name = f"medops_test_{uuid.uuid4().hex[:12]}"
    admin_url = override or DEFAULT_DATABASE_URL
    admin = create_async_engine(
        admin_url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield admin_url.rsplit("/", 1)[0] + f"/{name}"
    finally:
        async with admin.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :d AND pid <> pg_backend_pid()"
                ),
                {"d": name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        await admin.dispose()


@pytest.fixture()
async def db_engine(scratch_db: str) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(scratch_db)
    yield eng
    await eng.dispose()
