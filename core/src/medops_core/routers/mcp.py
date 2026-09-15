"""MCP registry + external API onboarding routes.

- /api/v1/mcp-servers        registry list / register / remove (P4b quick-config)
- /api/v1/endpoints          external API onboarding (P2.5)
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from medops_core.api_registry import ExternalApiClient
from medops_core.mcp_client.registry import MCPServerConfig
from medops_core.models import McpServer
from medops_core.schemas import McpServerIn


class EndpointIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    auth_type: str = "bearer"  # bearer | header | none
    api_header: str | None = None
    model: str | None = None
    kind: str = "generic"  # llm | generic
    enabled: bool = True


class EndpointPatch(BaseModel):
    enabled: bool


class EndpointCallIn(BaseModel):
    endpoint: str
    method: str = "GET"
    path: str = ""
    json_body: dict | None = None


def register(app: FastAPI) -> None:
    # ----------------------------------------------- MCP quick-config (P4b)
    @app.get("/api/v1/mcp-servers")
    async def list_mcp_servers() -> dict:
        return {"ok": True, "data": {"items": app.state.registry.list_status()}}

    @app.post("/api/v1/mcp-servers", status_code=201)
    async def register_mcp_server(body: McpServerIn) -> dict:
        # live registry: register + attempt connect (degrades to unavailable)
        handle = app.state.registry.register(
            MCPServerConfig(name=body.name, url=body.url)
        )
        await handle.connect()
        # persist via the async db factory (test-swappable), not RegistrySync
        # (which reads the real DATABASE_URL and would break test isolation).
        factory = app.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(McpServer).where(McpServer.name == body.name))
            ).first()
            if row is None:
                row = McpServer(name=body.name, endpoint=body.url)
                s.add(row)
            row.transport = "streamable-http"
            row.endpoint = body.url
            row.health = handle.state.value
            row.tool_list = [{"name": t} for t in handle.tools]
            row.last_heartbeat = datetime.now(UTC)
            await s.commit()
        return {"ok": True, "data": handle.status()}

    @app.delete("/api/v1/mcp-servers/{name}")
    async def remove_mcp_server(name: str) -> dict:
        removed = await app.state.registry.remove(name)
        db_deleted = False
        factory = app.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(McpServer).where(McpServer.name == name))
            ).first()
            if row is not None:
                await s.delete(row)
                await s.commit()
                db_deleted = True
        if not removed and not db_deleted:
            raise HTTPException(status_code=404, detail="mcp server not found")
        return {"ok": True, "data": {"deleted": name}}

    # ------------------------------------------------- external API onboarding
    @app.get("/api/v1/endpoints")
    async def list_endpoints() -> dict:
        eps = await app.state.endpoint_registry.list_endpoints(enabled_only=False)
        # never echo credentials back
        for e in eps:
            e["api_key"] = "***" if e["api_key"] else ""
        return {"count": len(eps), "endpoints": eps}

    @app.put("/api/v1/endpoints")
    async def upsert_endpoint(ep: EndpointIn) -> dict:
        if ep.auth_type not in ("bearer", "header", "none"):
            raise HTTPException(
                status_code=422, detail="auth_type must be bearer|header|none"
            )
        endpoint_id = await app.state.endpoint_registry.upsert(
            ep.name,
            ep.base_url,
            ep.api_key,
            auth_type=ep.auth_type,
            api_header=ep.api_header,
            model=ep.model,
            kind=ep.kind,
            enabled=ep.enabled,
        )
        return {"ok": True, "id": endpoint_id, "name": ep.name}

    @app.delete("/api/v1/endpoints/{name}")
    async def delete_endpoint(name: str) -> dict:
        deleted = await app.state.endpoint_registry.delete(name)
        if not deleted:
            raise HTTPException(status_code=404, detail="endpoint not found")
        return {"ok": True, "data": {"deleted": name}}

    @app.patch("/api/v1/endpoints/{name}")
    async def patch_endpoint(name: str, body: EndpointPatch) -> dict:
        await app.state.endpoint_registry.set_enabled(name, body.enabled)
        eps = await app.state.endpoint_registry.list_endpoints(enabled_only=False)
        match = [e for e in eps if e["name"] == name]
        if not match:
            raise HTTPException(status_code=404, detail="endpoint not found")
        match[0]["api_key"] = "***" if match[0]["api_key"] else ""
        return {"ok": True, "data": match[0]}

    @app.post("/api/v1/endpoints/call")
    async def call_endpoint(req: EndpointCallIn) -> dict:
        client: ExternalApiClient = app.state.external_api
        return await client.call(req.endpoint, req.method, req.path, req.json_body)
