"""MCP client registry: config-driven connections to multiple MCP servers.

Implements the first slice of design §9 layer 2: register servers, connect
concurrently (one failure never blocks others), poll health, and degrade to
UNAVAILABLE on timeout/error instead of raising.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from mcp.client import Client
from pydantic import BaseModel, Field


class ServerState(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"


class MCPServerConfig(BaseModel):
    """Connection config for one MCP server (streamable-http)."""

    name: str
    url: str = Field(description="Full streamable-http URL, including /mcp path")
    timeout_s: float = 3.0


ClientFactory = Callable[[MCPServerConfig], Any]


class ServerHandle:
    """State machine for one registered MCP server.

    UNKNOWN -> HEALTHY (connect+list_tools ok) or UNAVAILABLE (timeout/error).
    Never raises from connect/health_poll; call_tool raises RuntimeError when
    the server is UNAVAILABLE.
    """

    def __init__(self, config: MCPServerConfig, client_factory: ClientFactory | None = None):
        self.config = config
        self._client_factory = client_factory
        self.state = ServerState.UNKNOWN
        self.tools: list[str] = []
        self.last_error: str | None = None
        self._client: Any = None

    def _make_client(self) -> Any:
        if self._client_factory is not None:
            client = self._client_factory(self.config)
            if client is not None:
                return client
        return Client(self.config.url)

    async def connect(self) -> bool:
        """Connect and cache the tool list. Failure -> UNAVAILABLE, no raise."""
        try:
            client = self._make_client()
            await asyncio.wait_for(client.__aenter__(), timeout=self.config.timeout_s)
            try:
                result = await asyncio.wait_for(
                    client.list_tools(), timeout=self.config.timeout_s
                )
            except Exception:
                await client.__aexit__(None, None, None)
                raise
            self._client = client
            self.tools = [t.name for t in result.tools]
            self.state = ServerState.HEALTHY
            self.last_error = None
            return True
        except Exception as exc:  # noqa: BLE001 - degradation is the contract
            self.state = ServerState.UNAVAILABLE
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._client = None
            return False

    async def health_poll_once(self) -> bool:
        """Call the server's health_check tool. Failure -> UNAVAILABLE."""
        if self._client is None:
            self.state = ServerState.UNAVAILABLE
            self.last_error = "not connected"
            return False
        try:
            await asyncio.wait_for(
                self._client.call_tool("health_check", {}), timeout=self.config.timeout_s
            )
            self.state = ServerState.HEALTHY
            self.last_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self.state = ServerState.UNAVAILABLE
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> Any:
        """Call a tool; raises RuntimeError if the server is UNAVAILABLE."""
        if self.state == ServerState.UNAVAILABLE or self._client is None:
            raise RuntimeError(
                f"MCP server '{self.config.name}' is unavailable "
                f"(state={self.state.value}, last_error={self.last_error})"
            )
        r = await asyncio.wait_for(
            self._client.call_tool(name, args or {}), timeout=self.config.timeout_s
        )
        # mcp-sdk-v2 note §4: dict results arrive as JSON text content.
        return r.structured_content if r.structured_content else json.loads(r.content[0].text)

    def status(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "url": self.config.url,
            "state": self.state.value,
            "tools": list(self.tools),
            "last_error": self.last_error,
        }


class MCPRegistry:
    """Config-driven registry of MCP servers (design §9 layer 2)."""

    def __init__(self, client_factory: ClientFactory | None = None):
        self._handles: dict[str, ServerHandle] = {}
        self._client_factory = client_factory

    def set_client_factory(self, factory: ClientFactory) -> None:
        """Inject a client factory (tests: route to in-process MCPServer)."""
        self._client_factory = factory

    def register(self, config: MCPServerConfig) -> ServerHandle:
        handle = ServerHandle(config, client_factory=self._client_factory)
        self._handles[config.name] = handle
        return handle

    async def connect_all(self) -> None:
        """Connect all servers concurrently; one failure never blocks others."""
        await asyncio.gather(
            *(h.connect() for h in self._handles.values()), return_exceptions=True
        )

    def get(self, name: str) -> ServerHandle:
        if name not in self._handles:
            raise KeyError(f"no MCP server registered under name '{name}'")
        return self._handles[name]

    def list_status(self) -> list[dict[str, Any]]:
        return [h.status() for h in self._handles.values()]
