"""DB layer tests: engine connectivity + alembic upgrade/downgrade round-trip.

PG mode: uses the local dev PG on 127.0.0.1:55432 (see deploy/README-pg.md);
each test run creates/drops a scratch database. Falls back to SQLite tmp file
when MEDOPS_TEST_DB_URL is set to a sqlite URL.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from medops_core.db import DEFAULT_DATABASE_URL, make_engine, make_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "core" / "alembic.ini"

PG_AVAILABLE = os.environ.get("MEDOPS_TEST_DB_URL", DEFAULT_DATABASE_URL).startswith(
    "postgresql"
)


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": db_url}
    return subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
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
async def engine(scratch_db: str) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(scratch_db)
    yield eng
    await eng.dispose()


async def test_engine_select_1(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_session_factory_select_1(engine: AsyncEngine) -> None:
    factory = make_session_factory(engine)
    async with factory() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_alembic_upgrade_downgrade_roundtrip(scratch_db: str) -> None:
    proc = _run_alembic(scratch_db, "upgrade", "head")
    assert "0001" in proc.stderr + proc.stdout

    eng = make_engine(scratch_db)
    async with eng.connect() as conn:
        tables = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            if scratch_db.startswith("postgresql")
            else text("SELECT name FROM sqlite_master WHERE type = 'table'")
        )
        names = {row[0] for row in tables.fetchall()}
    await eng.dispose()
    assert "alembic_version" in names

    current = _run_alembic(scratch_db, "current")
    assert "head" in current.stdout

    _run_alembic(scratch_db, "downgrade", "base")
    current = _run_alembic(scratch_db, "current")
    assert "0001" not in current.stdout
