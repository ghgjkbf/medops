"""P4a: delete/cleanup API tests — single delete (work orders/alerts/plans/
records/devices), bulk cleanup by age (alerts/logs/metrics), chat history clear.

Covers 404/422/child-nullify/device-cascade/count assertions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from medops_core.app import create_app
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import (
    Alert,
    Base,
    ChatMessage,
    ChatSession,
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

OLD = datetime(2020, 1, 1, tzinfo=UTC)
NOW = datetime.now(UTC)


@pytest.fixture()
def client(db_engine: AsyncEngine) -> TestClient:
    """App wired to a scratch DB with seeded rows (all sync, one loop)."""
    sync_engine = sync_create_engine(_sync_url(str(db_engine.url)))
    Base.metadata.create_all(sync_engine)

    from sqlalchemy.orm import sessionmaker

    seed_factory = sessionmaker(bind=sync_engine, expire_on_commit=False)
    with seed_factory() as s:
        s.add_all([
            Device(device_id="ct-sim-01", device_type="ct", model="SimCT-9",
                   department="radiology", status="online"),
            Device(device_id="dr-sim-01", device_type="dr", model="SimDR-2",
                   department="radiology", status="error"),
        ])
        s.flush()

        w1 = WorkOrder(device_id="ct-sim-01", title="replace tube",
                       description="", status="pending")
        s.add(w1)
        s.flush()

        s.add_all([
            Alert(device_id="ct-sim-01", level="critical", message="tube overheat",
                  attribution="rule", work_order_id=w1.id, created_at=OLD),
            Alert(device_id="ct-sim-01", level="info", message="export ok",
                  work_order_id=w1.id, created_at=NOW),
            Alert(device_id="dr-sim-01", level="critical", message="detector fail",
                  created_at=NOW),
        ])
        s.add(MaintenancePlan(device_id="ct-sim-01", name="quarterly PM",
                              interval_days=90))
        s.add(MaintenanceRecord(device_id="ct-sim-01", work_order_id=w1.id,
                                kind="pm", content="cleaned filters"))
        s.add_all([
            DeviceLog(device_id="ct-sim-01", ts=OLD, level="ERROR",
                      message="old overheat"),
            DeviceLog(device_id="ct-sim-01", ts=NOW, level="INFO",
                      message="recent heartbeat"),
        ])
        s.add_all([
            DeviceMetric(device_id="ct-sim-01", ts=OLD, metric_name="tube_temp",
                         value=99.0),
            DeviceMetric(device_id="ct-sim-01", ts=NOW, metric_name="tube_temp",
                         value=45.0),
        ])
        cs = ChatSession(session_key="sess-1")
        s.add(cs)
        s.flush()
        s.add(ChatMessage(session_id=cs.id, role="user", content="hello"))
        s.commit()
    sync_engine.dispose()

    application = create_app()
    engine = create_async_engine(str(db_engine.url), poolclass=NullPool)
    application.state.endpoint_registry = _StubRegistry()
    application.state.db_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield TestClient(application)
    engine.dispose()


class _StubRegistry:
    handles: list = []

    def list_status(self) -> list[dict]:
        return []


# ----------------------------------------------------------- work orders
def test_delete_work_order_200(client: TestClient) -> None:
    wo = client.get("/api/v1/work-orders").json()["data"]["items"][0]
    r = client.delete(f"/api/v1/work-orders/{wo['id']}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/v1/work-orders").json()["data"]["total"] == 0


def test_delete_work_order_404(client: TestClient) -> None:
    assert client.delete("/api/v1/work-orders/9999").status_code == 404


def test_delete_work_order_nullifies_children(client: TestClient) -> None:
    wo = client.get("/api/v1/work-orders").json()["data"]["items"][0]
    client.delete(f"/api/v1/work-orders/{wo['id']}")
    # alerts that referenced it now have work_order_id null
    for a in client.get("/api/v1/alerts").json()["data"]["items"]:
        assert a["work_order_id"] is None
    # maintenance records referencing it also nulled
    recs = client.get("/api/v1/maintenance-records").json()["data"]["items"]
    assert recs and all(r["work_order_id"] is None for r in recs)


# ----------------------------------------------------------------- alerts
def test_delete_alert_200(client: TestClient) -> None:
    alert = client.get("/api/v1/alerts").json()["data"]["items"][0]
    r = client.delete(f"/api/v1/alerts/{alert['id']}")
    assert r.status_code == 200
    remaining = {a["id"] for a in client.get("/api/v1/alerts").json()["data"]["items"]}
    assert alert["id"] not in remaining


def test_delete_alert_404(client: TestClient) -> None:
    assert client.delete("/api/v1/alerts/9999").status_code == 404


def test_bulk_delete_alerts_by_level(client: TestClient) -> None:
    r = client.delete("/api/v1/alerts?level=critical")
    body = r.json()
    assert r.status_code == 200 and body["data"]["deleted"] == 2
    assert client.get("/api/v1/alerts").json()["data"]["total"] == 1


def test_bulk_delete_alerts_by_device(client: TestClient) -> None:
    r = client.delete("/api/v1/alerts?device_id=dr-sim-01")
    assert r.json()["data"]["deleted"] == 1
    left = client.get("/api/v1/alerts").json()["data"]["items"]
    assert all(a["device_id"] != "dr-sim-01" for a in left)


def test_bulk_delete_alerts_before(client: TestClient) -> None:
    r = client.delete("/api/v1/alerts?before=2024-01-01T00:00:00")
    assert r.json()["data"]["deleted"] == 1  # only the OLD alert
    assert client.get("/api/v1/alerts").json()["data"]["total"] == 2


def test_bulk_delete_alerts_invalid_before_422(client: TestClient) -> None:
    assert client.delete("/api/v1/alerts?before=not-a-date").status_code == 422


# ------------------------------------------------------------------ logs
def test_bulk_delete_logs_before(client: TestClient) -> None:
    r = client.delete("/api/v1/logs?before=2024-01-01T00:00:00")
    assert r.json()["data"]["deleted"] == 1
    left = client.get("/api/v1/logs").json()["data"]["items"]
    assert len(left) == 1 and left[0]["message"] == "recent heartbeat"


# ---------------------------------------------------------------- metrics
def test_bulk_delete_metrics_before(client: TestClient) -> None:
    r = client.delete("/api/v1/metrics?before=2024-01-01T00:00:00")
    assert r.json()["data"]["deleted"] == 1
    left = client.get("/api/v1/metrics").json()["data"]["items"]
    assert len(left) == 1 and left[0]["value"] == 45.0


# -------------------------------------------------------- plans and records
def test_delete_maintenance_plan_200_and_404(client: TestClient) -> None:
    plan = client.get("/api/v1/maintenance-plans").json()["data"]["items"][0]
    assert client.delete(f"/api/v1/maintenance-plans/{plan['id']}").status_code == 200
    assert client.delete(f"/api/v1/maintenance-plans/{plan['id']}").status_code == 404


def test_delete_maintenance_record_200_and_404(client: TestClient) -> None:
    rec = client.get("/api/v1/maintenance-records").json()["data"]["items"][0]
    assert client.delete(f"/api/v1/maintenance-records/{rec['id']}").status_code == 200
    assert client.delete(f"/api/v1/maintenance-records/{rec['id']}").status_code == 404


# ----------------------------------------------------------------- devices
def test_delete_device_cascades(client: TestClient) -> None:
    r = client.delete("/api/v1/devices/ct-sim-01")
    assert r.status_code == 200
    assert client.get("/api/v1/alerts").json()["data"]["total"] == 1  # only dr
    assert client.get("/api/v1/work-orders").json()["data"]["total"] == 0
    assert client.get("/api/v1/maintenance-plans").json()["data"]["total"] == 0
    assert client.get("/api/v1/maintenance-records").json()["data"]["total"] == 0
    assert client.get("/api/v1/logs").json()["data"]["total"] == 0
    assert client.get("/api/v1/metrics").json()["data"]["total"] == 0
    # other device untouched
    assert client.get("/api/v1/devices").json()["data"]["total"] == 1


def test_delete_device_404(client: TestClient) -> None:
    assert client.delete("/api/v1/devices/nope").status_code == 404


# ------------------------------------------------------------ chat history
def test_clear_chat_sessions(client: TestClient, db_engine: AsyncEngine) -> None:
    r = client.delete("/api/v1/chat-sessions")
    assert r.status_code == 200 and r.json()["data"]["deleted"] >= 1
    # messages cascade-deleted alongside sessions (chat_message.session_id CASCADE)
    from sqlalchemy import text

    sync_engine = sync_create_engine(_sync_url(str(db_engine.url)))
    with sync_engine.connect() as conn:
        sessions = conn.execute(text("SELECT count(*) FROM chat_session")).scalar()
        messages = conn.execute(text("SELECT count(*) FROM chat_message")).scalar()
    sync_engine.dispose()
    assert sessions == 0 and messages == 0
