"""P2-4: inspector agent + scheduler tests (FakeLLM + in-process mini server)."""

from __future__ import annotations

import pytest
from mcp.client import Client
from mcp.server import MCPServer
from medops_core.agents.inspector import InspectorAgent, evaluate_metrics
from medops_core.agents.llm import FakeLLM
from medops_core.agents.scheduler import InspectionScheduler
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig
from medops_core.models import Alert, Base, Device, WorkOrder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


# -------------------------------------------------------------- mini fixture
def _mini_ct_server(tube_temp: float) -> MCPServer:
    mcp = MCPServer("mini-ct")

    @mcp.tool()
    def get_tube_stats() -> dict:
        return {
            "available": True,
            "device_id": "ct-sim-01",
            "metrics": {"tube_temp": tube_temp, "tube_exposure_count": 10.0},
            "status": "error" if tube_temp >= 45 else "ok",
            "simulated": True,
        }

    @mcp.tool()
    def health_check() -> dict:
        return {"status": "ok"}

    return mcp


def _mini_dr_server(detector_temp: float) -> MCPServer:
    mcp = MCPServer("mini-dr")

    @mcp.tool()
    def get_detector_temp() -> dict:
        return {
            "available": True,
            "device_id": "dr-sim-01",
            "detector_temp": detector_temp,
            "status": "ok",
            "simulated": True,
        }

    @mcp.tool()
    def health_check() -> dict:
        return {"status": "ok"}

    return mcp


@pytest.fixture()
async def registry(db_engine: AsyncEngine, tube_temp: float = 36.0) -> MCPRegistry:
    reg = MCPRegistry(client_factory=lambda cfg: Client(_mini_ct_server(tube_temp)))
    reg.register(MCPServerConfig(name="ct", url="in-process://ct"))
    await reg.connect_all()
    yield reg


@pytest.fixture()
def session_factory(db_engine: AsyncEngine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed_devices(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from medops_core.db import make_session_factory

    factory = make_session_factory(db_engine)
    async with factory() as s, s.begin():
        s.add(Device(device_id="ct-sim-01", device_type="ct"))


# ------------------------------------------------------------------- metrics
def test_evaluate_metrics_ok() -> None:
    hits = evaluate_metrics("ct-sim-01", {"tube_temp": 36.0})
    assert hits == []


def test_evaluate_metrics_warning() -> None:
    hits = evaluate_metrics("ct-sim-01", {"tube_temp": 41.0})
    assert len(hits) == 1
    assert hits[0].level == "warning"
    assert "tube_temp" in hits[0].message


def test_evaluate_metrics_error() -> None:
    hits = evaluate_metrics("ct-sim-01", {"tube_temp": 46.0})
    assert hits[0].level == "error"


# ----------------------------------------------------------------- inspector
async def test_inspection_healthy_no_anomalies(
    db_engine: AsyncEngine, session_factory, tube_temp: float = 36.0
) -> None:
    await _seed_devices(db_engine)
    reg = MCPRegistry(client_factory=lambda cfg: Client(_mini_ct_server(36.0)))
    reg.register(MCPServerConfig(name="ct", url="in-process://ct"))
    await reg.connect_all()

    agent = InspectorAgent(FakeLLM(), reg, session_factory)
    result = await agent.run_inspection()
    assert result.checked_servers == ["ct"]
    assert result.anomalies == []
    assert result.alerts_created == 0
    assert result.work_orders_created == []


async def test_inspection_anomaly_creates_alert_and_work_order(
    db_engine: AsyncEngine, session_factory
) -> None:
    await _seed_devices(db_engine)
    reg = MCPRegistry(client_factory=lambda cfg: Client(_mini_ct_server(66.0)))
    reg.register(MCPServerConfig(name="ct", url="in-process://ct"))
    await reg.connect_all()

    agent = InspectorAgent(FakeLLM(text="球管过热，建议更换风扇"), reg, session_factory)
    result = await agent.run_inspection()
    assert result.alerts_created == 1
    assert len(result.work_orders_created) == 1
    assert result.provider_used == "fake"

    async with session_factory() as s:
        alert = (await s.execute(select(Alert))).scalars().one()
        assert alert.device_id == "ct-sim-01"
        assert alert.attribution.startswith("[fake]")
        order = (await s.execute(select(WorkOrder))).scalars().one()
        assert alert.work_order_id == order.id  # alert linked to auto-created order


async def test_inspection_dedupes_repeated_runs(
    db_engine: AsyncEngine, session_factory
) -> None:
    await _seed_devices(db_engine)
    reg = MCPRegistry(client_factory=lambda cfg: Client(_mini_ct_server(66.0)))
    reg.register(MCPServerConfig(name="ct", url="in-process://ct"))
    await reg.connect_all()
    agent = InspectorAgent(FakeLLM(), reg, session_factory)

    await agent.run_inspection()
    await agent.run_inspection()  # second run

    async with session_factory() as s:
        orders = (await s.execute(select(WorkOrder))).scalars().all()
        assert len(orders) == 1  # dedupe_key prevents duplicates


async def test_inspection_llm_down_rule_fallback(
    db_engine: AsyncEngine, session_factory
) -> None:
    await _seed_devices(db_engine)
    reg = MCPRegistry(client_factory=lambda cfg: Client(_mini_ct_server(66.0)))
    reg.register(MCPServerConfig(name="ct", url="in-process://ct"))
    await reg.connect_all()

    fake = FakeLLM(fail_providers={"fake"})
    agent = InspectorAgent(fake, reg, session_factory)
    result = await agent.run_inspection()
    assert result.alerts_created == 1
    async with session_factory() as s:
        alert = (await s.execute(select(Alert))).scalars().one()
        assert alert.attribution.startswith("[规则降级]")


async def test_inspection_multi_server(
    db_engine: AsyncEngine, session_factory
) -> None:
    await _seed_devices(db_engine)
    servers = {"ct": _mini_ct_server(66.0), "dr": _mini_dr_server(28.0)}

    def factory(cfg):  # noqa: ANN001, ANN202
        return Client(servers[cfg.name])

    reg = MCPRegistry(client_factory=factory)
    reg.register(MCPServerConfig(name="ct", url="in-process://ct"))
    reg.register(MCPServerConfig(name="dr", url="in-process://dr"))
    await reg.connect_all()

    agent = InspectorAgent(FakeLLM(), reg, session_factory)
    result = await agent.run_inspection()
    assert result.checked_servers == ["ct", "dr"]
    assert result.alerts_created == 1  # only ct's tube_temp is out of range


# ---------------------------------------------------------------- scheduler
async def test_scheduler_start_stop_idempotent(
    db_engine: AsyncEngine, session_factory
) -> None:
    await _seed_devices(db_engine)
    reg = MCPRegistry(client_factory=lambda cfg: Client(_mini_ct_server(36.0)))
    reg.register(MCPServerConfig(name="ct", url="in-process://ct"))
    await reg.connect_all()
    inspector = InspectorAgent(FakeLLM(), reg, session_factory)
    sched = InspectionScheduler(inspector, interval_s=60)

    sched.start()
    assert sched.running is True
    sched.start()  # idempotent
    assert sched.running is True
    sched.stop()
    assert sched.running is False
    sched.stop()  # no-op


async def test_scheduler_run_now_manual(
    db_engine: AsyncEngine, session_factory
) -> None:
    await _seed_devices(db_engine)
    reg = MCPRegistry(client_factory=lambda cfg: Client(_mini_ct_server(36.0)))
    reg.register(MCPServerConfig(name="ct", url="in-process://ct"))
    await reg.connect_all()
    inspector = InspectorAgent(FakeLLM(), reg, session_factory)
    sched = InspectionScheduler(inspector, interval_s=60)
    result = await sched.run_now()
    assert result.checked_servers == ["ct"]
    assert sched.last_result is result
