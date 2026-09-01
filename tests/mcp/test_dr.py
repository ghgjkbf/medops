"""DR MCP server tests (P1-6): Client lifetime inside test body."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.client import Client
from mcp_dr import build_server


def _payload(result) -> dict:
    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


def _write_snapshot(outbox: Path, metrics: dict[str, float]) -> None:
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "metrics_snapshot.json").write_text(
        json.dumps(
            {
                "device_id": "dr-sim-01",
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
    assert {"health_check", "check_generator_status", "get_detector_temp"} <= names


async def test_generator_status_ok(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path, {"generator_kvp": 120.5, "generator_mas": 100.0, "detector_temp": 28.0}
    )
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("check_generator_status", {}))
    assert r["available"] is True and r["status"] == "ok"
    assert r["metrics"]["generator_kvp"] == 120.5


async def test_generator_status_warning(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, {"generator_kvp": 126.0, "generator_mas": 100.0})
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("check_generator_status", {}))
    assert r["status"] == "warning"  # 126 >= warn_high 125


async def test_detector_temp_error(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, {"detector_temp": 46.0})
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_detector_temp", {}))
    assert r["available"] is True
    assert r["detector_temp"] == 46.0
    assert r["status"] == "error"  # 46 >= error_high 45


async def test_missing_snapshot(tmp_path: Path) -> None:
    server = build_server(outbox=tmp_path / "nope")
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_detector_temp", {}))
    assert r["available"] is False
