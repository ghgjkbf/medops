"""P4c: Agent knowledge tools — secretary search_knowledge / get_fault_report
and inspector attribution enrichment from the knowledge base."""

from __future__ import annotations

import pytest
from medops_core import knowledge
from medops_core.agents.llm import FakeLLM
from medops_core.agents.secretary import SecretaryAgent, classify_intent
from medops_core.mcp_client.registry import MCPRegistry
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import Alert, Base, Device, DeviceLog, WorkOrder
from medops_core.reporting import device_fault_report
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture()
def factory(db_engine: AsyncEngine) -> async_sessionmaker:
    sync_engine = sync_create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    return async_sessionmaker(
        create_async_engine(
            db_engine.url.render_as_string(hide_password=False), poolclass=NullPool
        ),
        expire_on_commit=False,
    )


@pytest.fixture()
async def seeded(factory: async_sessionmaker) -> async_sessionmaker:
    await knowledge.seed_builtin(factory)
    return factory


# ------------------------------------------------------------------- intents
def test_intent_how_to_handle_hits_knowledge() -> None:
    assert classify_intent("球管过热怎么处理").tool == "search_knowledge"


def test_intent_report_hits_fault_report() -> None:
    assert classify_intent("给我 ct 的故障报告").tool == "get_fault_report"


def test_intent_plain_status_unchanged() -> None:
    assert classify_intent("球管温度多少").tool == "get_tube_stats"
    assert classify_intent("现在有告警吗").tool == "query_alerts"


# ------------------------------------------------------- secretary plan() end
async def test_secretary_kb_tool_in_trajectory(seeded: async_sessionmaker) -> None:
    agent = SecretaryAgent(FakeLLM(), MCPRegistry(), db_factory=seeded)
    result = await agent.run("球管过热怎么处理")
    calls = [c for c in result.tool_trajectory if c.name == "search_knowledge"]
    assert calls and calls[0].ok and calls[0].result
    assert any("球管" in h["title"] for h in calls[0].result)


async def test_secretary_report_tool_in_trajectory(seeded: async_sessionmaker) -> None:
    async with seeded() as s:
        s.add(Device(device_id="ct-sim-01", device_type="ct"))
        await s.commit()
    agent = SecretaryAgent(FakeLLM(), MCPRegistry(), db_factory=seeded)
    result = await agent.run("给我 ct 的故障报告")
    calls = [c for c in result.tool_trajectory if c.name == "get_fault_report"]
    assert calls and calls[0].ok
    assert "ct-sim-01" in str(calls[0].result.get("scope"))


# ------------------------------------------------------------- fault report
async def test_device_fault_report_content(seeded: async_sessionmaker) -> None:
    async with seeded() as s:
        s.add(Device(device_id="ct-sim-01", device_type="ct"))
        s.add(Alert(device_id="ct-sim-01", level="critical", message="tube overheat"))
        s.add(DeviceLog(device_id="ct-sim-01", level="ERROR", message="cooling degraded"))
        s.add(WorkOrder(device_id="ct-sim-01", title="replace tube"))
        await s.commit()

    report = await device_fault_report(seeded, "ct 的故障报告")
    assert report["scope"] == ["ct-sim-01"]
    assert "tube overheat" in report["markdown"]
    assert "cooling degraded" in report["markdown"]
    assert "replace tube" in report["markdown"]
    assert report["alert_count"] == 1 and report["work_order_count"] == 1


async def test_device_fault_report_scope_filters(seeded: async_sessionmaker) -> None:
    async with seeded() as s:
        s.add(Device(device_id="ct-sim-01", device_type="ct"))
        s.add(Device(device_id="dr-sim-01", device_type="dr"))
        s.add(Alert(device_id="dr-sim-01", level="warning", message="detector drift"))
        await s.commit()

    report = await device_fault_report(seeded, "ct 的报告")
    assert report["scope"] == ["ct-sim-01"]
    assert "dr-sim-01" not in report["markdown"]
    assert "detector drift" not in report["markdown"]


# -------------------------------------------------------- inspector enrichment
async def test_enrich_alerts_appends_kb_hint(seeded: async_sessionmaker) -> None:
    async with seeded() as s:
        alert = Alert(
            device_id="ct-sim-01", level="warning",
            message="tube_temp=64.75 exceeded error threshold",
            attribution="规则归因：温度超限",
        )
        s.add(alert)
        await s.commit()
        alert_id = alert.id

    await knowledge.enrich_alerts(seeded, [alert])
    assert "知识库建议" in alert.attribution
    assert "球管" in alert.attribution  # seeded CT doc matched by tube keywords

    async with seeded() as s:
        row = (await s.get(Alert, alert_id))
        assert row is not None and "知识库建议" in (row.attribution or "")


async def test_enrich_alerts_no_hit_untouched(factory: async_sessionmaker) -> None:
    async with factory() as s:
        alert = Alert(device_id="ct-sim-01", level="info", message="量子纠缠异常")
        s.add(alert)
        await s.commit()
    original = alert.attribution
    await knowledge.enrich_alerts(factory, [alert])
    assert alert.attribution == original
