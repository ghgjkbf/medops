"""Ventilator MCP server tests (P1-7): Client lifetime inside test body."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.client import Client
from mcp_ventilator import build_server


def _payload(result) -> dict:
    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


def _write_snapshot(outbox: Path, metrics: dict[str, float]) -> None:
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "metrics_snapshot.json").write_text(
        json.dumps(
            {
                "device_id": "ventilator-sim-01",
                "ts": "2026-09-01T00:00:00+00:00",
                "t": 10.0,
                "metrics": metrics,
            }
        ),
        encoding="utf-8",
    )


async def test_list_tools(tmp_path: Path) -> None:
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools.tools}
    assert {"health_check", "get_realtime_params", "run_self_test"} <= names


async def test_realtime_params_ok(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path, {"o2_concentration": 93.0, "tidal_volume": 500.0, "airway_pressure": 15.0}
    )
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_realtime_params", {}))
    assert r["available"] is True and r["status"] == "ok"
    assert r["params"]["o2_concentration"] == 93.0


async def test_realtime_params_o2_warning(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path, {"o2_concentration": 88.0, "tidal_volume": 500.0, "airway_pressure": 15.0}
    )
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_realtime_params", {}))
    assert r["status"] == "warning"  # 88 < warn_low 90, >= error_low 85


async def test_self_test_pass_and_fail(tmp_path: Path) -> None:
    server = build_server(outbox=tmp_path)
    _write_snapshot(
        tmp_path, {"o2_concentration": 93.0, "tidal_volume": 500.0, "airway_pressure": 15.0}
    )
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("run_self_test", {}))
    assert r["overall"] == "pass"

    # leak scenario: pressure/volume out of range -> corresponding subsystems fail
    _write_snapshot(
        tmp_path, {"o2_concentration": 93.0, "tidal_volume": 380.0, "airway_pressure": 11.0}
    )
    async with Client(server.mcp) as client:
        r2 = _payload(await client.call_tool("run_self_test", {}))
    assert r2["overall"] == "pass"  # still above error thresholds

    _write_snapshot(
        tmp_path, {"o2_concentration": 93.0, "tidal_volume": 200.0, "airway_pressure": 7.0}
    )
    async with Client(server.mcp) as client:
        r3 = _payload(await client.call_tool("run_self_test", {}))
    assert r3["overall"] == "fail"
    assert r3["subsystems"]["flow_sensor"] == "fail"
    assert r3["subsystems"]["pressure_circuit"] == "fail"
    assert r3["subsystems"]["o2_cell"] == "pass"


async def test_missing_snapshot(tmp_path: Path) -> None:
    server = build_server(outbox=tmp_path / "nope")
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_realtime_params", {}))
    assert r["available"] is False
