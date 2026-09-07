"""P3-2: report generation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from medops_core.agents.llm import FakeLLM
from medops_core.app import create_app
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import Alert, Base, Device, WorkOrder
from medops_core.reporting import generate_report
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


def _seed(db_engine: AsyncEngine) -> None:
    engine = sync_create_engine(_sync_url(str(db_engine.url)))
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        s.add_all([
            Device(device_id="ct-sim-01", device_type="ct", model="m",
                   department="d", status="online"),
            Device(device_id="dr-sim-01", device_type="dr", model="m",
                   department="d", status="error"),
        ])
        s.add_all([
            Alert(device_id="ct-sim-01", level="critical", message="overheat",
                  created_at=now - timedelta(hours=1)),
            Alert(device_id="ct-sim-01", level="info", message="ok",
                  created_at=now - timedelta(days=3)),  # outside 24h window
        ])
        s.add_all([
            WorkOrder(device_id="ct-sim-01", title="t1", description="",
                      status="pending"),
            WorkOrder(device_id="dr-sim-01", title="t2", description="",
                      status="closed"),
        ])
        s.commit()
    engine.dispose()


async def test_report_counts_and_markdown(db_engine: AsyncEngine) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    _seed(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    report = await generate_report(factory, hours=24)
    detail = report["detail"]
    assert detail["device_status_counts"] == {"online": 1, "error": 1}
    assert detail["alerts"]["total"] == 1  # old alert excluded
    assert detail["alerts"]["critical"] == 1
    assert detail["work_orders"]["open"] == 1
    assert detail["work_orders"]["closed"] == 1
    md = report["markdown"]
    assert "巡检报告" in md and "告警统计" in md and "工单统计" in md


async def test_report_llm_polish(db_engine: AsyncEngine) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    _seed(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    llm = FakeLLM(text="[polished] 报告")
    report = await generate_report(factory, hours=24, llm=llm)
    assert report["markdown"] == "[polished] 报告"


async def test_report_llm_failure_degrades_to_template(db_engine: AsyncEngine) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    _seed(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    llm = FakeLLM(text="[should not appear]", fail_providers={"fake"})
    report = await generate_report(factory, hours=24, llm=llm)
    assert "巡检报告" in report["markdown"]  # template kept


def test_report_endpoint(db_engine: AsyncEngine) -> None:
    _seed(db_engine)
    application = create_app()
    engine = create_async_engine(str(db_engine.url), poolclass=NullPool)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    application.state.db_factory = async_sessionmaker(engine, expire_on_commit=False)
    client = TestClient(application)
    r = client.post("/api/v1/reports/generate?hours=24")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "巡检报告" in body["data"]["markdown"]
    # invalid window
    assert client.post("/api/v1/reports/generate?hours=0").status_code == 422
