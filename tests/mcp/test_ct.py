"""CT MCP server tests (P1-5): in-process Client; Client lifetime inside test body."""

from __future__ import annotations

import socket
from pathlib import Path

from mcp.client import Client
from mcp_ct import build_server
from medops_engine.dicom_writer import write_study


def _payload(result) -> dict:
    import json

    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_list_tools(seeded_db=None) -> None:
    server = build_server(outbox="unused")
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools.tools}
    assert {"health_check", "check_dicom_dir", "get_tube_stats", "check_pacs_connectivity"} <= names


async def test_check_dicom_dir_valid(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    write_study(outbox, n_files=3)
    server = build_server(outbox=outbox)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("check_dicom_dir", {}))
    assert r["exists"] is True
    assert r["total"] == 3 and r["valid"] == 3 and r["corrupt"] == 0
    assert r["latest_mtime"] is not None


async def test_check_dicom_dir_corrupt(tmp_path: Path) -> None:
    # write_study names files SIM_000N per call, so a second call to the same
    # dir would overwrite; generate the corrupt file in a subdir and move it in.
    outbox = tmp_path / "outbox"
    write_study(outbox, n_files=2)
    corrupt_dir = tmp_path / "corrupt"
    write_study(corrupt_dir, n_files=1, corrupt=True)
    src = next(iter(corrupt_dir.glob("*.dcm")))
    src.replace(outbox / "corrupted.dcm")
    server = build_server(outbox=outbox)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("check_dicom_dir", {}))
    assert r["total"] == 3
    assert r["valid"] == 2 and r["corrupt"] == 1


async def test_check_dicom_dir_missing(tmp_path: Path) -> None:
    server = build_server(outbox=tmp_path / "nope")
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("check_dicom_dir", {}))
    assert r["exists"] is False and r["total"] == 0


async def test_get_tube_stats_from_snapshot(tmp_path: Path) -> None:
    import json

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "metrics_snapshot.json").write_text(
        json.dumps(
            {
                "device_id": "ct-sim-01",
                "ts": "2026-09-01T00:00:00+00:00",
                "t": 10.0,
                "metrics": {"tube_temp": 41.5, "tube_exposure_count": 120.0},
            }
        ),
        encoding="utf-8",
    )
    server = build_server(outbox=outbox)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_tube_stats", {}))
    assert r["available"] is True
    assert r["metrics"]["tube_temp"] == 41.5
    assert r["status"] == "warning"  # 41.5 >= warn_high 40, < error_high 45
    assert r["per_metric"]["tube_temp"] == "warning"


async def test_get_tube_stats_missing_snapshot(tmp_path: Path) -> None:
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("get_tube_stats", {}))
    assert r["available"] is False
    assert "simulator running" in r["reason"]


async def test_check_pacs_unreachable(tmp_path: Path) -> None:
    port = _free_port()  # nothing listening
    server = build_server(outbox=tmp_path)
    async with Client(server.mcp) as client:
        r = _payload(await client.call_tool("check_pacs_connectivity", {"port": port}))
    assert r["reachable"] is False
