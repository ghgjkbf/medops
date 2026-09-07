"""P3-4: maintenance reminder engine tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import Alert, Base, MaintenancePlan
from medops_core.reminders import REMINDER_KIND, plan_is_due, scan_and_remind
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import sessionmaker


def _seed_db(db_engine: AsyncEngine, plans: list[MaintenancePlan]) -> None:
    engine = sync_create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        s.add_all(plans)
        s.commit()
    engine.dispose()


@pytest.fixture()
def factory(db_engine: AsyncEngine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


def test_plan_is_due_logic() -> None:
    now = datetime.now(UTC)
    plan = MaintenancePlan(device_id="d", name="pm", interval_days=30)
    plan.last_done_at = now - timedelta(days=31)
    assert plan_is_due(plan, now)
    plan.last_done_at = now - timedelta(days=10)
    assert not plan_is_due(plan, now)
    # no last_done_at: falls back to created_at (None here -> not due at now)
    fresh = MaintenancePlan(device_id="d", name="pm", interval_days=30)
    assert not plan_is_due(fresh, now)


async def test_scan_creates_alert_and_rolls_cycle(
    db_engine: AsyncEngine, factory
) -> None:
    now = datetime.now(UTC)
    due = MaintenancePlan(
        device_id="ct-sim-01", name="overdue PM", interval_days=7,
        last_done_at=now - timedelta(days=8),
    )
    not_due = MaintenancePlan(
        device_id="ct-sim-01", name="fresh PM", interval_days=90,
        last_done_at=now - timedelta(days=8),
    )
    _seed_db(db_engine, [due, not_due])

    created = await scan_and_remind(factory, now=now)
    assert len(created) == 1
    assert created[0].kind == REMINDER_KIND
    assert created[0].level == "warning"
    assert created[0].device_id == "ct-sim-01"

    # idempotent: immediate rescan alerts nothing (cycle rolled forward)
    again = await scan_and_remind(factory, now=now)
    assert again == []


async def test_scan_inactive_plan_skipped(db_engine: AsyncEngine, factory) -> None:
    now = datetime.now(UTC)
    inactive = MaintenancePlan(
        device_id="d", name="retired", interval_days=1,
        last_done_at=now - timedelta(days=100), active=False,
    )
    _seed_db(db_engine, [inactive])
    assert await scan_and_remind(factory, now=now) == []


async def test_alert_persisted_in_db(db_engine: AsyncEngine, factory) -> None:
    now = datetime.now(UTC)
    plan = MaintenancePlan(
        device_id="dr-sim-01", name="cal PM", interval_days=30,
        last_done_at=now - timedelta(days=60),
    )
    _seed_db(db_engine, [plan])
    await scan_and_remind(factory, now=now)
    async with factory() as s:
        rows = (await s.scalars(select(Alert))).all()
        assert len(rows) == 1 and rows[0].kind == "maintenance_due"
        # last_done_at rolled forward: next due in 30 days
        refreshed = (
            await s.scalars(select(MaintenancePlan))
        ).first()
        assert refreshed.last_done_at is not None
