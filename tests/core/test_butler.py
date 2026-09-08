"""P5a: butler agent (inspector's management role) — deterministic operation
intents, LOW/HIGH risk gating (HIGH → pending confirmation → token executes),
FSM enforcement, audit trail, secretary delegation, REST endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from medops_core.agents.butler import ButlerAgent
from medops_core.agents.inspector import InspectionResult
from medops_core.agents.llm import FakeLLM
from medops_core.agents.secretary import SecretaryAgent
from medops_core.app import create_app
from medops_core.mcp_client.registry import MCPRegistry
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import Alert, Base, ButlerAudit, Device, DeviceLog, WorkOrder
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


class _StubScheduler:
    def __init__(self) -> None:
        self.calls = 0

    async def run_now(self) -> InspectionResult:
        self.calls += 1
        r = InspectionResult()
        r.checked_servers = ["ct"]
        r.alerts_created = 0
        return r


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
    async with factory() as s:
        s.add(Device(device_id="ct-sim-01", device_type="ct"))
        s.add(WorkOrder(device_id="ct-sim-01", title="replace tube", status="pending"))
        s.add(WorkOrder(device_id="ct-sim-01", title="in progress one", status="in_progress"))
        s.add(Alert(device_id="ct-sim-01", level="critical", message="tube overheat"))
        s.add(DeviceLog(device_id="ct-sim-01", level="ERROR", message="old"))
        await s.commit()
    return factory


@pytest.fixture()
def butler(seeded: async_sessionmaker) -> ButlerAgent:
    return ButlerAgent(db_factory=seeded, registry=MCPRegistry(), scheduler=_StubScheduler())


# ----------------------------------------------------------------- classify
def test_classify_transition(butler: ButlerAgent) -> None:
    op, args = butler._classify("把工单 14 转处理中")
    assert op == "transition_work_order"
    assert args["wo_id"] == 14 and args["status"] == "in_progress"


def test_classify_cleanup_days(butler: ButlerAgent) -> None:
    op, args = butler._classify("清理 30 天前的日志")
    assert op == "cleanup_data"
    assert args["resource"] == "logs" and args["days"] == 30


def test_classify_unsupported(butler: ButlerAgent) -> None:
    assert butler._classify("今天天气怎么样")[0] is None


# ------------------------------------------------------------- LOW executes
async def test_low_risk_transition_executes(
    seeded: async_sessionmaker, butler: ButlerAgent
) -> None:
    result = await butler.execute_task("把工单 1 转处理中")
    assert result["status"] == "executed" and result["risk"] == "low"
    assert result["result"]["ok"] is True
    async with seeded() as s:
        row = await s.get(WorkOrder, 1)
        assert row is not None and row.status == "in_progress"
    async with seeded() as s:
        rows = (await s.scalars(select(ButlerAudit))).all()
        assert len(rows) == 1 and rows[0].tool == "transition_work_order"


async def test_transition_illegal_fsm_ok_false(
    seeded: async_sessionmaker, butler: ButlerAgent
) -> None:
    result = await butler.execute_task("关闭工单 1")  # pending -> closed is illegal
    assert result["status"] == "executed" and result["result"]["ok"] is False
    assert "illegal" in result["result"]["error"]


# --------------------------------------------------- HIGH risk confirmation
async def test_high_risk_delete_alert_pending(
    seeded: async_sessionmaker, butler: ButlerAgent
) -> None:
    result = await butler.execute_task("删除告警 1")
    assert result["status"] == "pending_confirmation"
    assert result["token"] and result["operation"] == "delete_alert"
    async with seeded() as s:  # nothing deleted yet
        assert await s.get(Alert, 1) is not None


async def test_confirm_with_token_executes(seeded: async_sessionmaker, butler: ButlerAgent) -> None:
    pending = await butler.execute_task("删除告警 1")
    result = await butler.execute_task("删除告警 1", confirm_token=pending["token"])
    assert result["status"] == "executed" and result["confirmed"] is True
    async with seeded() as s:
        assert await s.get(Alert, 1) is None


async def test_confirm_unknown_token(seeded: async_sessionmaker, butler: ButlerAgent) -> None:
    result = await butler.execute_task("删除告警 1", confirm_token="nope")
    assert result["status"] == "error"


async def test_token_ttl_expiry(seeded: async_sessionmaker, butler: ButlerAgent) -> None:
    pending = await butler.execute_task("删除告警 1")
    # force-expire the pending entry
    op, args, _ = butler._pending[pending["token"]]
    butler._pending[pending["token"]] = (op, args, 0.0)
    result = await butler.execute_task("删除告警 1", confirm_token=pending["token"])
    assert result["status"] == "error" and "expired" in result["error"]


async def test_auto_mode_executes_high_risk(
    seeded: async_sessionmaker, butler: ButlerAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEDOPS_BUTLER_AUTO", "1")
    result = await butler.execute_task("删除告警 1")
    assert result["status"] == "executed" and result["confirmed"] is True


# ----------------------------------------------------------------- cleanups
async def test_cleanup_logs_by_days(seeded: async_sessionmaker, butler: ButlerAgent) -> None:
    pending = await butler.execute_task("清理 365 天前的日志")
    result = await butler.execute_task("清理 365 天前的日志", confirm_token=pending["token"])
    assert result["result"]["ok"] is True


async def test_delete_device_cascades(seeded: async_sessionmaker, butler: ButlerAgent) -> None:
    pending = await butler.execute_task("删除设备 ct-sim-01")
    result = await butler.execute_task("删除设备 ct-sim-01", confirm_token=pending["token"])
    assert result["result"]["ok"] is True
    async with seeded() as s:
        assert (await s.scalars(select(Device))).all() == []
        assert (await s.scalars(select(WorkOrder))).all() == []


# ----------------------------------------------------------------- triggers
async def test_trigger_inspection_with_stub(
    seeded: async_sessionmaker, butler: ButlerAgent
) -> None:
    result = await butler.execute_task("立即巡检")
    assert result["status"] == "executed" and result["result"]["ok"] is True
    assert butler._scheduler is not None and butler._scheduler.calls == 1


async def test_trigger_inspection_no_scheduler(seeded: async_sessionmaker) -> None:
    butler = ButlerAgent(db_factory=seeded)
    result = await butler.execute_task("立即巡检")
    assert result["result"]["ok"] is False


# -------------------------------------------------------- secretary delegate
async def test_secretary_delegates_to_butler(seeded: async_sessionmaker) -> None:
    butler = ButlerAgent(db_factory=seeded, scheduler=_StubScheduler())
    agent = SecretaryAgent(FakeLLM(), MCPRegistry(), butler=butler)
    result = await agent.run("把工单 1 转处理中")
    calls = [c for c in result.tool_trajectory if c.name == "butler"]
    assert calls and calls[0].ok
    assert calls[0].result["status"] == "executed"


# -------------------------------------------------------------------- REST
def test_rest_butler_task_and_audit(db_engine: AsyncEngine) -> None:
    sync_engine = sync_create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    application = create_app()
    engine = create_async_engine(
            db_engine.url.render_as_string(hide_password=False), poolclass=NullPool
        )
    application.state.db_factory = async_sessionmaker(engine, expire_on_commit=False)
    application.state.endpoint_registry = _StubRegistry()
    application.state.butler = ButlerAgent(
        db_factory=application.state.db_factory, scheduler=_StubScheduler()
    )
    client = TestClient(application)
    r = client.post("/api/v1/butler/task", json={"task": "立即巡检"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "executed"
    audit = client.get("/api/v1/butler/audit").json()["data"]["items"]
    assert len(audit) == 1 and audit[0]["tool"] == "trigger_inspection"
    engine.dispose()


class _StubRegistry:
    handles: list = []

    def list_status(self) -> list[dict]:
        return []
