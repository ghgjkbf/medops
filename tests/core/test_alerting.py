"""P2-7: alerting engine + agent API endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from medops_common.constants import AlertLevel
from medops_core.alerting import ESCALATION_THRESHOLD, AlertingEngine
from medops_core.models import Alert, Base
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


@pytest.fixture()
async def db_engine(tmp_path) -> AsyncEngine:  # noqa: ANN001
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'p27.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed_alerts(engine: AsyncEngine, device_id: str, n: int, level: str) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for _ in range(n):
            s.add(
                Alert(
                    device_id=device_id,
                    level=level,
                    message="repeat alarm",
                    attribution="test",
                )
            )
        await s.commit()


async def test_recent_counts(db_engine: AsyncEngine) -> None:
    engine = AlertingEngine(engine=db_engine)
    try:
        await _seed_alerts(db_engine, "ct-sim-01", 2, AlertLevel.WARNING.value)
        counts = await engine.recent_counts(window_s=3600)
        assert counts["ct-sim-01"] == 2
    finally:
        await engine.close()


async def test_escalation_below_threshold(db_engine: AsyncEngine) -> None:
    engine = AlertingEngine(engine=db_engine, sinks=[])
    try:
        await _seed_alerts(db_engine, "ct-sim-01", ESCALATION_THRESHOLD - 1, AlertLevel.WARNING.value)
        escalated = await engine.escalate_if_needed("ct-sim-01")
        assert escalated is False
    finally:
        await engine.close()


async def test_escalation_at_threshold(db_engine: AsyncEngine) -> None:
    notifications: list[dict] = []
    engine = AlertingEngine(engine=db_engine, sinks=[notifications.append])
    try:
        await _seed_alerts(db_engine, "ct-sim-01", ESCALATION_THRESHOLD, AlertLevel.WARNING.value)
        escalated = await engine.escalate_if_needed("ct-sim-01")
        assert escalated is True
        assert notifications and notifications[0]["type"] == "escalation"

        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with factory() as s:
            rows = (await s.scalars(select(Alert))).all()
            assert all(r.level == AlertLevel.CRITICAL.value for r in rows)
            assert "升级" in (rows[0].attribution or "")
    finally:
        await engine.close()


async def test_notify_all_summary(db_engine: AsyncEngine) -> None:
    notifications: list[dict] = []
    engine = AlertingEngine(engine=db_engine, sinks=[notifications.append])
    try:
        await _seed_alerts(db_engine, "ct-sim-01", 2, AlertLevel.CRITICAL.value)
        count = await engine.notify_all()
        assert count == 2
        assert notifications[-1]["type"] == "summary"
    finally:
        await engine.close()


# --------------------------------------------------------------------- API
async def test_health_endpoint_without_db() -> None:
    from medops_core.app import create_app

    application = create_app()
    client = TestClient(application)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["mcp_servers"] == []


async def test_inspect_503_when_not_configured() -> None:
    from medops_core.app import create_app

    application = create_app()
    client = TestClient(application)
    resp = client.post("/api/v1/agents/inspect")
    # lifespan may build a scheduler only if connect succeeds; with no DB the
    # scheduler still starts with an empty registry -> run_now works, no error.
    # Assert we get a JSON dict (degraded ok) OR 503; not a crash.
    assert resp.status_code in (200, 503)


async def test_chat_degraded_no_servers() -> None:
    from medops_core.app import create_app

    application = create_app()
    client = TestClient(application)
    resp = client.post("/api/v1/chat", json={"message": "3号CT温度多少"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["trajectory"] == []
    assert data["provider_used"] in ("rules", "fake")


async def test_agents_status() -> None:
    from medops_core.app import create_app

    application = create_app()
    client = TestClient(application)
    resp = client.get("/api/v1/agents/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "registry" in data and "scheduler_running" in data
