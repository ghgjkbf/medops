"""Tests for MCPRegistry: multi-server register / health poll / timeout degradation.

Per docs/notes/mcp-sdk-v2-api.md the in-process partner is a plain
``MCPServer`` instance handed to ``Client`` — no ports, no subprocesses.
The registry accepts an injectable ``client_factory`` so tests can route a
config URL to an in-process server, while a bogus ``127.0.0.1`` URL exercises
the real network failure path (UNAVAILABLE, never raises).
"""

import json
import socket

import pytest
from mcp.server import MCPServer
from medops_core.mcp_client.registry import (
    MCPRegistry,
    MCPServerConfig,
    ServerState,
)


def _make_partner_server() -> MCPServer:
    """Minimal in-process MCP server with health_check + echo tools."""
    server = MCPServer("test-partner")

    @server.tool()
    def health_check() -> dict:
        """Liveness probe used by the registry health poller."""
        return {"status": "ok"}

    @server.tool()
    def echo(message: str) -> dict:
        """Echo the message back."""
        return {"echo": message}

    return server


def _unused_tcp_url() -> str:
    """A 127.0.0.1 URL on a port that is closed again immediately."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/mcp"


@pytest.fixture
def partner_server() -> MCPServer:
    return _make_partner_server()


@pytest.fixture
def registry(partner_server: MCPServer) -> MCPRegistry:
    reg = MCPRegistry()

    def client_factory(config: MCPServerConfig):
        # Route the "partner" config in-process; everything else uses the
        # default real Client(url) behaviour.
        if config.url == "inproc://partner":
            from mcp.client import Client

            return Client(partner_server)
        return None  # None -> registry falls back to Client(config.url)

    reg.set_client_factory(client_factory)
    return reg


async def test_connect_lists_tools(registry: MCPRegistry) -> None:
    registry.register(MCPServerConfig(name="partner", url="inproc://partner"))
    await registry.connect_all()

    handle = registry.get("partner")
    assert handle.state == ServerState.HEALTHY
    assert set(handle.tools) == {"health_check", "echo"}


async def test_call_tool_echo_roundtrip(registry: MCPRegistry) -> None:
    registry.register(MCPServerConfig(name="partner", url="inproc://partner"))
    await registry.connect_all()

    result = await registry.get("partner").call_tool("echo", {"message": "ping"})
    assert result == {"echo": "ping"}


async def test_health_poll_marks_healthy(registry: MCPRegistry) -> None:
    registry.register(MCPServerConfig(name="partner", url="inproc://partner"))
    await registry.connect_all()

    handle = registry.get("partner")
    ok = await handle.health_poll_once()
    assert ok is True
    assert handle.state == ServerState.HEALTHY


async def test_bad_url_marked_unavailable_connect_all_does_not_raise(
    registry: MCPRegistry,
) -> None:
    bad = MCPServerConfig(name="dead", url=_unused_tcp_url(), timeout_s=0.5)
    registry.register(bad)
    await registry.connect_all()  # must not raise

    handle = registry.get("dead")
    assert handle.state == ServerState.UNAVAILABLE
    assert handle.last_error is not None


async def test_one_bad_server_does_not_block_healthy_one(
    registry: MCPRegistry,
) -> None:
    registry.register(MCPServerConfig(name="dead", url=_unused_tcp_url(), timeout_s=0.5))
    registry.register(MCPServerConfig(name="partner", url="inproc://partner"))
    await registry.connect_all()

    assert registry.get("dead").state == ServerState.UNAVAILABLE
    assert registry.get("partner").state == ServerState.HEALTHY


async def test_call_tool_on_unavailable_raises(registry: MCPRegistry) -> None:
    registry.register(MCPServerConfig(name="dead", url=_unused_tcp_url(), timeout_s=0.5))
    await registry.connect_all()

    with pytest.raises(RuntimeError, match="dead"):
        await registry.get("dead").call_tool("echo", {"message": "x"})


async def test_list_status(registry: MCPRegistry) -> None:
    registry.register(MCPServerConfig(name="dead", url=_unused_tcp_url(), timeout_s=0.5))
    registry.register(MCPServerConfig(name="partner", url="inproc://partner"))
    await registry.connect_all()

    status = {s["name"]: s for s in registry.list_status()}
    assert status["dead"]["state"] == ServerState.UNAVAILABLE.value
    assert status["partner"]["state"] == ServerState.HEALTHY.value
    assert "echo" in status["partner"]["tools"]


async def test_dict_result_compatibility(partner_server: MCPServer) -> None:
    """Note §4 compatibility: dict results come back as JSON text content."""
    from mcp.client import Client

    async with Client(partner_server) as client:
        r = await client.call_tool("echo", {"message": "hi"})
        data = (
            r.structured_content
            if r.structured_content
            else json.loads(r.content[0].text)
        )
        assert data == {"echo": "hi"}
