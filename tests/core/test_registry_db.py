"""P1-10: registry <-> mcp_server table persistence tests (scratch PG DB)."""

from __future__ import annotations

import pytest
from mcp.client import Client
from mcp.server import MCPServer
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig, ServerState
from medops_core.mcp_client.sync import RegistrySync
from medops_core.models import Base, McpServer
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture()
async def initialized_db(db_engine: AsyncEngine) -> AsyncEngine:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield db_engine
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture()
def sync(initialized_db: AsyncEngine) -> RegistrySync:
    return RegistrySync(database_url=str(initialized_db.url))


def _mini_server() -> MCPServer:
    mcp = MCPServer("mini")

    @mcp.tool()
    def echo(msg: str) -> str:
        """Echo a message."""
        return msg

    @mcp.tool()
    def health_check() -> dict:
        """Mini health probe."""
        return {"status": "ok"}

    return mcp


async def test_upsert_and_load(sync: RegistrySync) -> None:
    sync.upsert_server("ct-server", "http://127.0.0.1:8801/mcp", "HEALTHY", ["get_tube_stats"])
    sync.upsert_server("ct-server", "http://127.0.0.1:8801/mcp", "UNAVAILABLE", [])
    rows = sync.load_all()
    assert len(rows) == 1
    assert rows[0]["health"] == "UNAVAILABLE"  # updated, not duplicated
    assert rows[0]["tools"] == []


async def test_record_heartbeat_no_insert(sync: RegistrySync) -> None:
    sync.record_heartbeat("ghost", "HEALTHY", [])  # not registered -> no row
    assert sync.load_all() == []


async def test_registry_persists_across_restart(sync: RegistrySync) -> None:
    server = _mini_server()

    def factory(config: MCPServerConfig):  # noqa: ANN001, ANN202
        return Client(server)

    # First lifecycle: register + connect + persist
    registry = MCPRegistry(client_factory=factory)
    registry.register(MCPServerConfig(name="mini", url="in-process://mini"))
    await registry.connect_all()
    handle = registry.get("mini")
    assert handle.state == ServerState.HEALTHY
    sync.upsert_server("mini", handle.config.url, handle.state.value, handle.tools)

    # Restart: fresh registry loads from DB
    rows = sync.load_all()
    registry2 = MCPRegistry(client_factory=factory)
    for row in rows:
        registry2.register(MCPServerConfig(name=row["name"], url=row["url"]))
    assert registry2.get("mini").config.url == "in-process://mini"

    # Health poll -> write back heartbeat
    await registry2.connect_all()
    ok = await registry2.get("mini").health_poll_once()
    assert ok is True
    sync.record_heartbeat("mini", "healthy", ["echo", "health_check"])
    rows2 = sync.load_all()
    assert rows2[0]["health"] == "healthy"
    assert "echo" in [t["name"] for t in rows2[0]["tools"]]


async def test_unavailable_state_written_back(sync: RegistrySync) -> None:
    sync.upsert_server("dead", "http://127.0.0.1:59999/mcp", "UNKNOWN", [])

    def dead_factory(config: MCPServerConfig):  # noqa: ANN001, ANN202
        return None  # force connect failure path

    registry = MCPRegistry(client_factory=dead_factory)
    registry.register(MCPServerConfig(name="dead", url="http://127.0.0.1:59999/mcp", timeout_s=0.5))
    await registry.connect_all()
    handle = registry.get("dead")
    assert handle.state == ServerState.UNAVAILABLE
    sync.record_heartbeat("dead", handle.state.value, [])
    rows = sync.load_all()
    assert rows[0]["health"] == "unavailable"  # ServerState.value is lowercase


async def test_row_count_matches(sync: RegistrySync, initialized_db: AsyncEngine) -> None:
    from sqlalchemy import select as sa_select
    from sqlalchemy.ext.asyncio import create_async_engine

    sync.upsert_server("a", "http://a/mcp", "HEALTHY", ["t1"])
    sync.upsert_server("b", "http://b/mcp", "HEALTHY", ["t2", "t3"])
    eng = create_async_engine(str(initialized_db.url))
    async with eng.connect() as conn:
        count = (await conn.execute(sa_select(McpServer.id))).scalars().all()
    await eng.dispose()
    assert len(count) == 2
