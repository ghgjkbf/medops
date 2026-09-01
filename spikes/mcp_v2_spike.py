"""MCP Python SDK v2 spike: MCPServer + streamable-http + stdio round-trip.

Usage:
  python spikes/mcp_v2_spike.py            # full spike (http + stdio)
  python spikes/mcp_v2_spike.py --stdio-server   # internal: run server on stdio
"""
import asyncio
import json
import socket
import sys
from importlib.metadata import version as pkg_version

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server import MCPServer

mcp_server = MCPServer("medops-spike")


@mcp_server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp_server.tool()
def get_device_status(device_id: str) -> dict:
    """Return mock device status."""
    return {"device_id": device_id, "online": True, "battery": 87}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_port(port: int, timeout: float = 10.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 1)
            w.close()
            return
        except OSError:
            await asyncio.sleep(0.1)
    raise TimeoutError(f"port {port} never came up")


async def verify_client(client: Client, label: str) -> None:
    tools = await client.list_tools()
    names = sorted(t.name for t in tools.tools)
    print(f"[{label}] tools: {names}")
    assert names == ["add", "get_device_status"], names
    for t in tools.tools:
        print(f"[{label}]   {t.name} schema: {json.dumps(t.input_schema)}")

    r = await client.call_tool("add", {"a": 1, "b": 2})
    print(f"[{label}] add(1,2) -> structured={r.structured_content}")
    assert not r.is_error
    assert r.structured_content["result"] == 3, r.structured_content

    r2 = await client.call_tool("get_device_status", {"device_id": "dev-007"})
    print(f"[{label}] get_device_status -> content[0].text={r2.content[0].text!r} structured={r2.structured_content}")
    assert not r2.is_error
    # v2 (2.1.1): plain dict returns are serialized into TextContent JSON;
    # structured_content is only populated for tools declaring an output schema.
    data = r2.structured_content if r2.structured_content else json.loads(r2.content[0].text)
    assert isinstance(data, dict)
    assert data["device_id"] == "dev-007"
    assert data["online"] is True


async def http_roundtrip() -> None:
    port = free_port()
    task = asyncio.create_task(
        mcp_server.run_streamable_http_async(host="127.0.0.1", port=port)
    )
    try:
        await wait_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        print(f"[http] server up at {url}")
        async with Client(url) as client:
            await verify_client(client, "http")
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def stdio_roundtrip() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=[__file__, "--stdio-server"]
    )
    async with Client(params) as client:
        await verify_client(client, "stdio")


async def main() -> None:
    await http_roundtrip()
    await stdio_roundtrip()
    print(f"SPIKE OK: mcp=={pkg_version('mcp')}")


if __name__ == "__main__":
    if "--stdio-server" in sys.argv:
        mcp_server.run("stdio")
    else:
        asyncio.run(main())
