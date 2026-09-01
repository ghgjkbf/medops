"""ECG MCP server tests (P1-8): Client lifetime inside test body."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.client import Client
from mcp_ecg import build_server


def _payload(result) -> dict:
    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


def _write_snapshot(outbox: Path, metrics: dict[str, float]) -> None:
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "metrics_snapshot.json").write_text(
        json.dumps(
            {
                "device_id": "ecg-sim-01",
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
    assert {"health_check", "get_waveform_quality", "check_export_files"} <= names


async def test_waveform_quality_ok(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, {"waveform_snr": 30.0, "heart_rate": 72.0, "battery_voltage": 12.6})
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_waveform_quality", {}))
    assert r["available"] is True and r["status"] == "ok"
    assert all(v == "attached" for v in r["leads"].values())


async def test_waveform_quality_lead_off(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, {"waveform_snr": 8.0, "heart_rate": 0.0, "battery_voltage": 12.6})
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_waveform_quality", {}))
    assert r["status"] == "error"  # 8 < error_low 12
    assert all(v == "off" for v in r["leads"].values())


async def test_battery_low_reflected_in_status(tmp_path: Path) -> None:
    # battery metrics are not part of waveform quality, but snapshot carries them;
    # waveform stays ok
    _write_snapshot(tmp_path, {"waveform_snr": 30.0, "heart_rate": 72.0, "battery_voltage": 11.2})
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_waveform_quality", {}))
    assert r["status"] == "ok"


async def test_check_export_files(tmp_path: Path) -> None:
    outbox = tmp_path / "exports"
    outbox.mkdir()
    (outbox / "ecg_export_001.xml").write_text("<ecg/>", encoding="utf-8")
    server = build_server(outbox=outbox)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("check_export_files", {}))
    assert r["exists"] is True and r["count"] == 1
    assert r["files"][0]["name"] == "ecg_export_001.xml"

    server2 = build_server(outbox=tmp_path / "nope")
    async with Client(server2.mcp) as client:
        r2 = _payload(await client.call_tool("check_export_files", {}))
    assert r2["exists"] is False
