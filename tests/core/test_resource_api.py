"""P3-1: resource CRUD API tests (devices/alerts/work-orders/plans/records/
logs/metrics) — pagination, filters, envelope, work-order state machine."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from medops_core.app import create_app
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import (
    Alert,
    Base,
    Device,
    DeviceLog,
    DeviceMetric,
    MaintenancePlan,
    MaintenanceRecord,
    WorkOrder,
)
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture()
def client(db_engine: AsyncEngine) -> TestClient:
    """App wired to a scratch DB with seeded rows (all sync, one loop)."""
    sync_engine = sync_create_engine(_sync_url(str(db_engine.url)))
    Base.metadata.create_all(sync_engine)

    # seed via sync sessions — no event loop involved
    from sqlalchemy.orm import sessionmaker

    seed_factory = sessionmaker(bind=sync_engine, expire_on_commit=False)
    with seed_factory() as s:
        s.add_all([
            Device(device_id="ct-sim-01", device_type="ct", model="SimCT-9",
                   department="radiology", status="online"),
            Device(device_id="dr-sim-01", device_type="dr", model="SimDR-2",
                   department="radiology", status="error"),
            Device(device_id="ven-sim-01", device_type="ventilator",
                   model="SimVen-1", department="icu", status="offline"),
        ])
        s.add_all([
            Alert(device_id="ct-sim-01", level="critical", message="tube overheat",
                  attribution="rule"),
            Alert(device_id="dr-sim-01", level="info", message="export ok"),
        ])
        s.add_all([
            WorkOrder(device_id="ct-sim-01", title="replace tube",
                      description="", status="pending"),
            WorkOrder(device_id="dr-sim-01", title="recalibrate",
                      description="", status="in_progress"),
        ])
        s.add(MaintenancePlan(device_id="ct-sim-01", name="quarterly PM",
                              interval_days=90))
        s.add(MaintenanceRecord(device_id="ct-sim-01", work_order_id=None,
                                kind="pm", content="cleaned filters"))
        s.add(DeviceLog(device_id="ct-sim-01", ts=datetime.now(UTC),
                        level="ERROR", message="overheat detected"))
        s.add(DeviceMetric(device_id="ct-sim-01", ts=datetime.now(UTC),
                           metric_name="tube_temp", value=59.5))
        s.commit()
    sync_engine.dispose()

    application = create_app()
    engine = create_async_engine(str(db_engine.url), poolclass=NullPool)
    application.state.endpoint_registry = _StubRegistry()
    application.state.db_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield TestClient(application)
    engine.dispose()


class _StubRegistry:
    """Minimal registry stub so lifespan MCP sync does not touch real servers."""

    handles: list = []

    def list_status(self) -> list[dict]:
        return []


# ---------------------------------------------------------------- devices
def test_list_devices_envelope_and_pagination(client: TestClient) -> None:
    r = client.get("/api/v1/devices?page=1&page_size=2")
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["data"]["total"] == 3
    assert len(body["data"]["items"]) == 2  # first page
    r2 = client.get("/api/v1/devices?page=2&page_size=2")
    assert len(r2.json()["data"]["items"]) == 1


def test_get_device_detail(client: TestClient) -> None:
    r = client.get("/api/v1/devices/ct-sim-01")
    assert r.status_code == 200
    assert r.json()["data"]["device_type"] == "ct"


def test_get_device_404(client: TestClient) -> None:
    assert client.get("/api/v1/devices/nope").status_code == 404


def test_create_device(client: TestClient) -> None:
    r = client.post("/api/v1/devices", json={
        "device_id": "ecg-sim-01", "device_type": "ecg", "model": "SimECG-1",
        "department": "cardiology",
    })
    assert r.status_code == 201
    assert client.get("/api/v1/devices/ecg-sim-01").status_code == 200


# ----------------------------------------------------------------- alerts
def test_list_alerts_filter_level(client: TestClient) -> None:
    body = client.get("/api/v1/alerts?level=critical").json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["message"] == "tube overheat"


def test_list_alerts_filter_device(client: TestClient) -> None:
    body = client.get("/api/v1/alerts?device_id=dr-sim-01").json()
    assert body["data"]["total"] == 1


# ------------------------------------------------------------ work orders
def test_create_work_order(client: TestClient) -> None:
    r = client.post("/api/v1/work-orders", json={
        "device_id": "ven-sim-01", "title": "check battery", "description": "",
    })
    assert r.status_code == 201
    assert r.json()["data"]["status"] == "pending"


def test_transition_work_order_legal(client: TestClient) -> None:
    wo = client.get("/api/v1/work-orders").json()["data"]["items"][0]
    r = client.patch(f"/api/v1/work-orders/{wo['id']}", json={"status": "in_progress"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "in_progress"


def test_transition_work_order_illegal_409(client: TestClient) -> None:
    wo = client.get("/api/v1/work-orders").json()["data"]["items"][0]
    r = client.patch(f"/api/v1/work-orders/{wo['id']}", json={"status": "closed"})
    assert r.status_code == 409


def test_transition_work_order_unknown_status_422(client: TestClient) -> None:
    wo = client.get("/api/v1/work-orders").json()["data"]["items"][0]
    r = client.patch(f"/api/v1/work-orders/{wo['id']}", json={"status": "bogus"})
    assert r.status_code == 422


# ------------------------------------------------------ plans and records
def test_list_maintenance_plans(client: TestClient) -> None:
    body = client.get("/api/v1/maintenance-plans").json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["interval_days"] == 90


def test_create_maintenance_plan(client: TestClient) -> None:
    r = client.post("/api/v1/maintenance-plans", json={
        "device_id": "dr-sim-01", "name":"monthly PM", "interval_days": 30,
    })
    assert r.status_code == 201


def test_list_maintenance_records(client: TestClient) -> None:
    body = client.get("/api/v1/maintenance-records").json()
    assert body["data"]["total"] == 1


def test_create_maintenance_record(client: TestClient) -> None:
    r = client.post("/api/v1/maintenance-records", json={
        "device_id": "dr-sim-01", "content": "sensor cleaned",
    })
    assert r.status_code == 201


# ------------------------------------------------------------- logs/metrics
def test_list_device_logs(client: TestClient) -> None:
    body = client.get("/api/v1/logs?device_id=ct-sim-01").json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["level"] == "ERROR"


def test_list_device_metrics(client: TestClient) -> None:
    body = client.get("/api/v1/metrics?device_id=ct-sim-01").json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["metric_name"] == "tube_temp"
    assert body["data"]["items"][0]["value"] == 59.5
