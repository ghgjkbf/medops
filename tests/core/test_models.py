"""ORM model tests (P1-2): create-all, insert/query per table, FK constraints.

Entity hydration requires the Session path (connection.execute(select(Entity))
returns plain Rows), so tests use async_sessionmaker — matching production usage.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from medops_common.constants import AlertLevel, DeviceType, WorkOrderStatus
from medops_core.models import (
    Alert,
    Base,
    ChatMessage,
    ChatSession,
    Device,
    DeviceLog,
    DeviceMetric,
    KnowledgeDoc,
    MaintenancePlan,
    MaintenanceRecord,
    McpServer,
    WorkOrder,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


@pytest.fixture()
async def initialized_engine(db_engine: AsyncEngine) -> AsyncEngine:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield db_engine
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture()
async def session_factory(initialized_engine: AsyncEngine) -> async_sessionmaker:
    from medops_core.db import make_session_factory

    return make_session_factory(initialized_engine)


async def test_core_tables_exist(initialized_engine: AsyncEngine) -> None:
    expected = {
        "mcp_server",
        "device",
        "device_metric",
        "device_log",
        "alert",
        "work_order",
        "maintenance_plan",
        "maintenance_record",
        "chat_session",
        "chat_message",
        "knowledge_doc",
        "api_endpoint",
    }
    assert expected == set(Base.metadata.tables.keys())


async def test_device_with_mcp_server_fk(session_factory) -> None:
    async with session_factory() as s, s.begin():
        s.add(
            McpServer(
                name="ct-server",
                endpoint="http://127.0.0.1:8801/mcp",
                tool_list=[{"name": "get_tube_stats"}],
            )
        )
        s.add(
            Device(
                device_id="ct-sim-01",
                device_type=DeviceType.CT.value,
                mcp_server_id=1,
            )
        )

    async with session_factory() as s:
        device = (
            await s.execute(select(Device).where(Device.device_id == "ct-sim-01"))
        ).scalars().one()
        assert device.device_type == DeviceType.CT.value
        assert device.mcp_server_id == 1
        assert device.mcp_server is not None
        assert device.mcp_server.name == "ct-server"


async def test_metric_composite_pk(session_factory) -> None:
    ts = datetime.now(UTC)
    async with session_factory() as s, s.begin():
        s.add(DeviceMetric(device_id="ct-sim-01", ts=ts, metric_name="tube_temp", value=36.5))

    with pytest.raises(IntegrityError):
        async with session_factory() as s, s.begin():
            s.add(
                DeviceMetric(
                    device_id="ct-sim-01", ts=ts, metric_name="tube_temp", value=37.0
                )
            )


async def test_work_order_state_machine_fields(session_factory) -> None:
    async with session_factory() as s, s.begin():
        s.add(WorkOrder(device_id="ct-sim-01", title="Tube overheat follow-up"))

    async with session_factory() as s:
        order = (await s.execute(select(WorkOrder).where(WorkOrder.id == 1))).scalars().one()
        assert order.status == WorkOrderStatus.PENDING.value


async def test_alert_links_work_order(session_factory) -> None:
    async with session_factory() as s, s.begin():
        s.add(WorkOrder(device_id="ct-sim-01", title="WO", dedupe_key="d-1"))
        s.add(
            Alert(
                device_id="ct-sim-01",
                level=AlertLevel.CRITICAL.value,
                message="tube temp critical",
                work_order_id=1,
            )
        )

    async with session_factory() as s:
        alert = (await s.execute(select(Alert).where(Alert.id == 1))).scalars().one()
        assert alert.work_order_id == 1
        assert alert.attribution is None  # LLM attribution lands in P2
        assert alert.work_order is not None
        assert alert.work_order.dedupe_key == "d-1"


async def test_remaining_tables_roundtrip(session_factory) -> None:
    """device_log / maintenance_plan / maintenance_record / chat_* / knowledge_doc."""
    async with session_factory() as s, s.begin():
        s.add(
            DeviceLog(device_id="ct-sim-01", level="ERROR", message="tube temp high")
        )
        s.add(
            MaintenancePlan(device_id="ct-sim-01", name="季度保养", interval_days=90)
        )
        s.add(
            MaintenanceRecord(device_id="ct-sim-01", kind="pm", content="更换球管冷却液")
        )
        s.add(ChatSession(session_key="s-1"))
        await s.flush()  # assign chat_session.id before ChatMessage (no relationship)
        s.add(ChatMessage(session_id=1, role="user", content="3号CT状态如何"))
        s.add(KnowledgeDoc(title="CT 日常巡检手册", content="..."))

    async with session_factory() as s:
        log = (await s.execute(select(DeviceLog).where(DeviceLog.id == 1))).scalars().one()
        assert log.ts is not None and log.ts.tzinfo is not None
        plan = (await s.execute(select(MaintenancePlan))).scalars().one()
        assert plan.active is True
        record = (await s.execute(select(MaintenanceRecord))).scalars().one()
        assert record.work_order_id is None
        msg = (await s.execute(select(ChatMessage))).scalars().one()
        assert msg.tool_trace is None
        doc = (await s.execute(select(KnowledgeDoc))).scalars().one()
        assert doc.meta == {}
