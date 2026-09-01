"""MCP server registry <-> database sync (P1-10).

Persists registry state to the ``mcp_server`` table (design §6): configs
loaded at startup, health/tool-list changes written back on poll. Uses a
dedicated sync engine (psycopg2) — same pattern as mcp_maintenance_db.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from medops_core.db import get_database_url
from medops_core.models import McpServer


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "+psycopg2").replace("+aiosqlite", "")


class RegistrySync:
    """Bidirectional sync between MCPRegistry and the mcp_server table."""

    def __init__(self, database_url: str | None = None) -> None:
        url = _sync_url(database_url or get_database_url())
        engine = create_engine(url, pool_pre_ping=True)
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    # ------------------------------------------------------------------ write
    def upsert_server(self, name: str, url: str, health: str, tools: list[str]) -> int:
        """Insert or update one mcp_server row; returns its id."""
        with self._session_factory() as s:
            row = s.scalars(select(McpServer).where(McpServer.name == name)).first()
            if row is None:
                row = McpServer(name=name, endpoint=url)
                s.add(row)
            row.transport = "streamable-http"
            row.endpoint = url
            row.health = health
            row.tool_list = [{"name": t} for t in tools]
            row.last_heartbeat = datetime.now(UTC)
            s.commit()
            return row.id

    def record_heartbeat(self, name: str, health: str, tools: list[str]) -> None:
        """Update health/tools/last_heartbeat after a poll (no insert)."""
        with self._session_factory() as s:
            row = s.scalars(select(McpServer).where(McpServer.name == name)).first()
            if row is None:
                return  # not registered; register_server owns creation
            row.health = health
            row.tool_list = [{"name": t} for t in tools]
            row.last_heartbeat = datetime.now(UTC)
            s.commit()

    # ------------------------------------------------------------------- read
    def load_all(self) -> list[dict]:
        """All registered servers as registry-ready dicts."""
        with self._session_factory() as s:
            rows = s.scalars(select(McpServer).order_by(McpServer.name)).all()
            return [
                {
                    "name": r.name,
                    "url": r.endpoint,
                    "health": r.health,
                    "tools": [t.get("name", "") for t in (r.tool_list or [])],
                    "last_heartbeat": (
                        r.last_heartbeat.isoformat() if r.last_heartbeat else None
                    ),
                }
                for r in rows
            ]
