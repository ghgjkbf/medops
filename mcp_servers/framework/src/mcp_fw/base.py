"""Base class for medops MCP servers (community extension point).

Wraps the MCP Python SDK v2 ``MCPServer``:
- instantiation (``MCPServer(name)``),
- a ``register_tool`` decorator passthrough,
- a built-in ``health_check`` tool (server name / status / tool list /
  last heartbeat from an injected provider),
- ``serve_http(host, port)`` → ``run_streamable_http_async`` (default path /mcp).

NOTE: all data served by medops MCP servers is SIMULATED
(毕业设计 / open-source demo — no real medical devices attached).
"""

from collections.abc import Callable

from mcp.server import MCPServer
from medops_common.schemas import Heartbeat

HeartbeatProvider = Callable[[], Heartbeat | None]


class MedopsMCPServer:
    """Base class for medops MCP servers.

    Subclasses register their tools in ``__init__`` via ``self.register_tool``
    (after calling ``super().__init__``), then either serve over
    streamable-http with ``serve_http`` or are connected in-process in tests
    via ``Client(server.mcp)``.
    """

    def __init__(
        self,
        name: str,
        heartbeat_provider: HeartbeatProvider | None = None,
    ) -> None:
        self.name = name
        self.mcp = MCPServer(name)
        self._heartbeat_provider = heartbeat_provider
        self._extra_tools: list[str] = []
        self.register_tool(self.health_check)

    # ------------------------------------------------------------------ tools
    def register_tool(self, fn: Callable | None = None, **kwargs):
        """Passthrough of ``MCPServer.tool()``; tracks tool names for health_check."""
        decorator = self.mcp.tool(**kwargs)
        if fn is None:

            def _wrap(f: Callable) -> Callable:
                self._extra_tools.append(kwargs.get("name") or f.__name__)
                return decorator(f)

            return _wrap
        self._extra_tools.append(kwargs.get("name") or fn.__name__)
        return decorator(fn)

    def health_check(self) -> dict:
        """Liveness probe: server name, status, registered tools, last heartbeat."""
        heartbeat = self._heartbeat_provider() if self._heartbeat_provider else None
        return {
            "server": self.name,
            "status": "ok",
            "tools": sorted(set(self._extra_tools)),
            "last_heartbeat": heartbeat.model_dump(mode="json") if heartbeat else None,
            "simulated": True,  # 模拟数据声明
        }

    # ----------------------------------------------------------------- serving
    async def serve_http(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        """Serve over streamable-http (default path /mcp).

        ``port=0`` is NOT supported by the SDK — pick a free port with a
        socket bind first if you need one (see docs/notes/mcp-sdk-v2-api.md §2).
        """
        await self.mcp.run_streamable_http_async(host=host, port=port)
