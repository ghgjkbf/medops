"""Offline golden-scenario checks (FakeLLM): routing correctness, no API cost."""

from __future__ import annotations

import pytest
from golden_scenarios import SCENARIOS, tool_hit
from mcp.client import Client
from mcp.server import MCPServer
from medops_core import knowledge
from medops_core.agents.llm import FakeLLM
from medops_core.agents.secretary import SecretaryAgent, classify_intent
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker


def _build_registry() -> MCPRegistry:
    """Mini servers exposing every tool the golden scenarios route to."""

    ct = MCPServer("ct")

    @ct.tool()
    def get_tube_stats() -> dict:
        return {"device_id": "ct-sim-01", "metrics": {"tube_temp": 36.5}, "status": "ok"}

    @ct.tool()
    def check_dicom_dir() -> dict:
        return {"exists": True, "total": 0, "valid": 0, "corrupt": 0}

    @ct.tool()
    def check_pacs_connectivity() -> dict:
        return {"reachable": False, "reason": "no pacs in eval"}

    vent = MCPServer("ventilator")

    @vent.tool()
    def get_realtime_params() -> dict:
        return {"device_id": "vent-sim-01", "params": {"o2_concentration": 93.0}, "status": "ok"}

    @vent.tool()
    def run_self_test() -> dict:
        return {"overall": "pass", "subsystems": {}}

    dr = MCPServer("dr")

    @dr.tool()
    def get_detector_temp() -> dict:
        return {"available": True, "device_id": "dr-sim-01", "detector_temp": 28.0, "status": "ok"}

    ecg = MCPServer("ecg")

    @ecg.tool()
    def get_waveform_quality() -> dict:
        return {"available": True, "waveform_snr": 30.0, "status": "ok", "leads": {}}

    mdb = MCPServer("maintenance-db")

    @mdb.tool()
    def query_alerts(level: str = "", since_iso: str = "") -> dict:
        return {"count": 0, "alerts": []}

    @mdb.tool()
    def get_maintenance_due(days_ahead: int = 30) -> dict:
        return {"count": 0, "plans": []}

    def factory(cfg):  # noqa: ANN001, ANN202
        servers = {"ct": ct, "ventilator": vent, "dr": dr, "ecg": ecg, "maintenance-db": mdb}
        return Client(servers[cfg.name])

    reg = MCPRegistry(client_factory=factory)
    for name in ("ct", "ventilator", "dr", "ecg", "maintenance-db"):
        reg.register(MCPServerConfig(name=name, url=f"in-process://{name}"))
    return reg


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.sid for s in SCENARIOS])
async def test_golden_scenario(scenario, db_engine) -> None:  # noqa: ANN001
    """Every scenario routes to the expected tool (or none) with FakeLLM."""
    intent = classify_intent(scenario.question)
    assert intent.tool == scenario.expected_tool, (
        f"{scenario.sid}: routed to {intent.tool}, expected {scenario.expected_tool}"
    )
    if scenario.expected_server is not None and intent.server is not None:
        assert intent.server == scenario.expected_server

    reg = _build_registry()
    await reg.connect_all()
    fake = FakeLLM(text="评估回答")
    from medops_core.mcp_client.sync import _sync_url  # noqa: PLC0415
    from medops_core.models import Base  # noqa: PLC0415

    sync_engine = sync_create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    await knowledge.seed_builtin(factory)  # builtin KB tools get real hits
    agent = SecretaryAgent(fake, reg, db_factory=factory)
    result = await agent.run(scenario.question)
    used = [c.name for c in result.tool_trajectory if c.ok]
    assert tool_hit(used, scenario.expected_tool), (
        f"{scenario.sid}: used {used}, expected {scenario.expected_tool}"
    )  # noqa: E501


async def test_eval_registry_connects_all() -> None:
    reg = _build_registry()
    await reg.connect_all()
    statuses = {h.config.name: h.state.value for h in reg.handles.values()}
    assert all(v == "healthy" for v in statuses.values())
