"""In-process integration tests for the device-status MCP server (Task 9).

Uses MCP SDK v2 in-process transport: ``Client(mcp_server_instance)`` — no
network, no subprocess, no real port. See docs/notes/mcp-sdk-v2-api.md §6.

NOTE: all device data is simulated (synthetic data only).
"""

import json
import sys

import pytest
from mcp.client import Client
from mcp_device_status.server import build_server


def _payload(result) -> dict:
    """Client-side compat: dict-returning tools land in content[0].text as JSON."""
    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


@pytest.fixture
def server():
    # Client(...) in-process transport expects the raw MCPServer instance.
    return build_server().mcp


async def test_list_tools_contains_expected_tools(server):
    async with Client(server) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools.tools}
    assert {"health_check", "get_process_status", "get_driver_info", "get_file_integrity"} <= names
    for t in tools.tools:
        assert t.input_schema["type"] == "object"


async def test_get_process_status_finds_current_python_process(server):
    async with Client(server) as client:
        r = await client.call_tool("get_process_status", {"process_name": "python"})
    data = _payload(r)
    assert data["found"] is True
    assert data["count"] >= 1
    assert any(p["pid"] == pytest.approx(sys.maxsize) or p["pid"] > 0 for p in data["processes"])
    for p in data["processes"]:
        assert "cpu_percent" in p and "memory_mb" in p and "status" in p


async def test_get_process_status_missing_process(server):
    async with Client(server) as client:
        r = await client.call_tool(
            "get_process_status", {"process_name": "definitely-not-a-real-process-xyz"}
        )
    data = _payload(r)
    assert data["found"] is False
    assert data["count"] == 0
    assert data["processes"] == []


async def test_get_file_integrity_existing_file(server, tmp_path):
    f = tmp_path / "sim-device.log"
    f.write_text("x" * 128)
    async with Client(server) as client:
        r = await client.call_tool(
            "get_file_integrity", {"path": str(f), "expected_min_bytes": 64}
        )
    data = _payload(r)
    assert data["exists"] is True
    assert data["size_bytes"] == 128
    assert data["ok"] is True
    assert data["mtime"] is not None


async def test_get_file_integrity_missing_file(server, tmp_path):
    missing = tmp_path / "nope.bin"
    async with Client(server) as client:
        r = await client.call_tool("get_file_integrity", {"path": str(missing)})
    data = _payload(r)
    assert data["exists"] is False
    assert data["ok"] is False
    assert data["size_bytes"] == 0


async def test_get_file_integrity_too_small(server, tmp_path):
    f = tmp_path / "tiny.log"
    f.write_text("x")
    async with Client(server) as client:
        r = await client.call_tool(
            "get_file_integrity", {"path": str(f), "expected_min_bytes": 100}
        )
    data = _payload(r)
    assert data["exists"] is True
    assert data["ok"] is False


async def test_get_driver_info_known_device(server):
    async with Client(server) as client:
        r = await client.call_tool("get_driver_info", {"device_id": "CT-001"})
    data = _payload(r)
    assert data["device_id"] == "CT-001"
    assert data["device_type"] == "ct"
    assert data["driver"]["version"]
    assert data["simulated"] is True


async def test_get_driver_info_unknown_device(server):
    async with Client(server) as client:
        r = await client.call_tool("get_driver_info", {"device_id": "ZZZ-999"})
    data = _payload(r)
    assert data["found"] is False
    assert data["simulated"] is True


async def test_health_check_ok(server):
    async with Client(server) as client:
        r = await client.call_tool("health_check", {})
    data = _payload(r)
    assert data["status"] == "ok"
    assert data["server"] == "medops-device-status"
    assert {"health_check", "get_process_status", "get_driver_info", "get_file_integrity"} <= set(
        data["tools"]
    )
    assert data["last_heartbeat"] is None
    assert data["simulated"] is True


async def test_health_check_with_heartbeat_provider():
    from medops_common.schemas import Heartbeat

    hb = Heartbeat(device_id="CT-001", seq=42)
    srv = build_server(heartbeat_provider=lambda: hb).mcp
    async with Client(srv) as client:
        r = await client.call_tool("health_check", {})
    data = _payload(r)
    assert data["status"] == "ok"
    assert data["last_heartbeat"]["device_id"] == "CT-001"
    assert data["last_heartbeat"]["seq"] == 42
