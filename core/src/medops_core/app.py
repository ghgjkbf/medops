"""medops-core FastAPI application skeleton (P0).

The registry is optional for P0: the app runs with no MCP servers registered,
in which case /api/v1/health reports an empty mcp_servers list.
"""

from fastapi import FastAPI

from medops_core.mcp_client.registry import MCPRegistry

app = FastAPI(title="medops-core")

# P0: no servers registered by default; wire real configs in a later task.
registry = MCPRegistry()


@app.get("/api/v1/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "medops-core",
        "mcp_servers": registry.list_status(),
    }
