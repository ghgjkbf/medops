"""Maintenance-db MCP server tests (P1-4): in-process Client + scratch PG DB.

Note: the Client (anyio task group) must be entered/exited inside the test
body — holding it across an async fixture yield breaks pytest-asyncio's
function-scoped event loop (cancel-scope task mismatch).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mcp.client import Client
from mcp_maintenance_db.server import MaintenanceDbServer
from medops_common.constants import AlertLevel
from medops_core.models import Alert, Base, Device, MaintenancePlan
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture()
async def seeded_db(db_engine: AsyncEngine) -> AsyncEngine:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from medops_core.db import make_session_factory

    factory = make_session_factory(db_engine)
    async with factory() as s, s.begin():
        s.add(
            Device(
                device_id="ct-sim-01",
                device_type="ct",
                model="SimCT 64",
                department="radiology",
            )
        )
        s.add(Device(device_id="ecg-sim-01", device_type="ecg", department="icu"))
        s.add(
            MaintenancePlan(
                device_id="ct-sim-01",
                name="季度保养",
                interval_days=90,
                last_done_at=datetime.now(UTC) - timedelta(days=85),
            )
        )
        s.add(
            MaintenancePlan(
                device_id="ecg-sim-01",
                name="月度检查",
                interval_days=30,
                last_done_at=datetime.now(UTC) - timedelta(days=45),  # overdue
            )
        )
    yield db_engine
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _payload(result) -> dict:
    """MCP v2: dict returns ride in content[0].text as JSON (API notes §4)."""
    import json

    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


async def test_server_registers_six_tools(seeded_db: AsyncEngine) -> None:
    server = MaintenanceDbServer(database_url=seeded_db.url.render_as_string(hide_password=False))
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools.tools}
    assert {
        "health_check",
        "query_devices",
        "get_maintenance_due",
        "create_work_order",
        "update_work_order",
        "add_repair_record",
        "query_alerts",
    } <= names


async def test_query_devices_filter(seeded_db: AsyncEngine) -> None:
    server = MaintenanceDbServer(database_url=seeded_db.url.render_as_string(hide_password=False))
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("query_devices", {}))
        assert r["count"] == 2
        r = _payload(await client.call_tool("query_devices", {"device_type": "ct"}))
        assert r["count"] == 1 and r["devices"][0]["device_id"] == "ct-sim-01"
        r = _payload(await client.call_tool("query_devices", {"department": "icu"}))
        assert r["count"] == 1 and r["devices"][0]["device_id"] == "ecg-sim-01"


async def test_get_maintenance_due(seeded_db: AsyncEngine) -> None:
    server = MaintenanceDbServer(database_url=seeded_db.url.render_as_string(hide_password=False))
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_maintenance_due", {"days_ahead": 30}))
        # ct plan: last done 85d ago, 90d interval -> due in ~5d (within window)
        # ecg plan: last done 45d ago, 30d interval -> overdue
        assert r["count"] == 2
        plans = {p["device_id"]: p for p in r["plans"]}
        assert plans["ecg-sim-01"]["overdue"] is True
        assert plans["ct-sim-01"]["overdue"] is False
        assert plans["ecg-sim-01"]["due_in_days"] < plans["ct-sim-01"]["due_in_days"]

        # Tight window: only the overdue one.
        r2 = _payload(await client.call_tool("get_maintenance_due", {"days_ahead": 0}))
        assert r2["count"] == 1 and r2["plans"][0]["device_id"] == "ecg-sim-01"


async def test_work_order_lifecycle_and_state_machine(seeded_db: AsyncEngine) -> None:
    server = MaintenanceDbServer(database_url=seeded_db.url.render_as_string(hide_password=False))
    async with Client(server.mcp) as client:
        r = _payload(
            await client.call_tool(
                "create_work_order",
                {
                    "device_id": "ct-sim-01",
                    "title": "Tube overheat follow-up",
                    "dedupe_key": "wo-ct-1",
                },
            )
        )
        assert r["created"] is True and r["action_risk"] == "low_risk_write"
        wo_id = r["work_order_id"]

        # dedupe: same key returns the same order without creating
        r2 = _payload(
            await client.call_tool(
                "create_work_order",
                {"device_id": "ct-sim-01", "title": "dup", "dedupe_key": "wo-ct-1"},
            )
        )
        assert r2["created"] is False and r2["work_order_id"] == wo_id

        # illegal jump: pending -> closed
        r3 = _payload(
            await client.call_tool(
                "update_work_order", {"work_order_id": wo_id, "new_status": "closed"}
            )
        )
        assert r3["updated"] is False and "illegal" in r3["error"]

        # legal chain: pending -> in_progress -> awaiting_verification -> closed
        for status in ("in_progress", "awaiting_verification", "closed"):
            r4 = _payload(
                await client.call_tool(
                    "update_work_order", {"work_order_id": wo_id, "new_status": status}
                )
            )
            assert r4["updated"] is True and r4["status"] == status

        # closed is terminal
        r5 = _payload(
            await client.call_tool(
                "update_work_order", {"work_order_id": wo_id, "new_status": "in_progress"}
            )
        )
        assert r5["updated"] is False


async def test_add_repair_record(seeded_db: AsyncEngine) -> None:
    server = MaintenanceDbServer(database_url=seeded_db.url.render_as_string(hide_password=False))
    async with Client(server.mcp) as client:
        r = _payload(
            await client.call_tool(
                "create_work_order",
                {"device_id": "ct-sim-01", "title": "WO for record"},
            )
        )
        wo_id = r["work_order_id"]
        r2 = _payload(
            await client.call_tool(
                "add_repair_record",
                {
                    "device_id": "ct-sim-01",
                    "content": "更换冷却风扇",
                    "work_order_id": wo_id,
                },
            )
        )
        assert r2["added"] is True and r2["record_id"] == 1

        # unknown work order rejected
        r3 = _payload(
            await client.call_tool(
                "add_repair_record",
                {"device_id": "ct-sim-01", "content": "x", "work_order_id": 999},
            )
        )
        assert r3["added"] is False


async def test_query_alerts_level_filter(seeded_db: AsyncEngine) -> None:
    server = MaintenanceDbServer(database_url=seeded_db.url.render_as_string(hide_password=False))
    with server._session_factory() as s:  # noqa: SLF001 (seed via server's own factory)
        s.add(
            Alert(
                device_id="ct-sim-01",
                level=AlertLevel.CRITICAL.value,
                message="tube temp critical",
            )
        )
        s.add(
            Alert(
                device_id="ecg-sim-01",
                level=AlertLevel.INFO.value,
                message="battery plan reminder",
            )
        )
        s.commit()

    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("query_alerts", {}))
        assert r["count"] == 2
        r = _payload(await client.call_tool("query_alerts", {"level": "critical"}))
        assert r["count"] == 1 and r["alerts"][0]["device_id"] == "ct-sim-01"
