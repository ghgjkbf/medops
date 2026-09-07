"""P4b: MCP server quick-config API (GET/POST/DELETE /api/v1/mcp-servers) +
external endpoint enable toggle (PATCH /api/v1/endpoints/{name})."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from medops_core.api_registry import EndpointRegistry, ExternalApiClient
from medops_core.app import create_app
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import Base
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture()
def client(db_engine: AsyncEngine) -> TestClient:
    """App wired to a scratch DB; no lifespan, so the live registry is empty."""
    sync_engine = sync_create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    application = create_app()
    engine = create_async_engine(
            db_engine.url.render_as_string(hide_password=False), poolclass=NullPool
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    application.state.db_factory = factory
    # endpoint routes use their own registry — point it at the scratch DB too
    # (create_app's default is module-level async_session_factory -> real DB,
    #  which raises "another operation is in progress" across event loops).
    application.state.endpoint_registry = EndpointRegistry(factory)
    application.state.external_api = ExternalApiClient(application.state.endpoint_registry)
    yield TestClient(application)
    engine.dispose()


def _db_server_names(db_engine: AsyncEngine) -> set[str]:
    from sqlalchemy import text

    sync_engine = sync_create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    with sync_engine.connect() as conn:
        names = {
            r[0] for r in conn.execute(text("SELECT name FROM mcp_server"))
        }
    sync_engine.dispose()
    return names


# ---------------------------------------------------------------- register
def test_register_mcp_server_200(client: TestClient, db_engine: AsyncEngine) -> None:
    r = client.post("/api/v1/mcp-servers", json={
        "name": "mcp-test-1", "url": "http://127.0.0.1:1/mcp",
    })
    assert r.status_code == 201 and r.json()["ok"] is True
    assert r.json()["data"]["name"] == "mcp-test-1"
    # persisted to mcp_server table
    assert "mcp-test-1" in _db_server_names(db_engine)
    # appears in list
    names = [s["name"] for s in client.get("/api/v1/mcp-servers").json()["data"]["items"]]
    assert "mcp-test-1" in names


def test_register_mcp_server_invalid_url_422(client: TestClient) -> None:
    r = client.post("/api/v1/mcp-servers", json={
        "name": "bad", "url": "ftp://not-http",
    })
    assert r.status_code == 422


def test_register_mcp_server_empty_name_422(client: TestClient) -> None:
    r = client.post("/api/v1/mcp-servers", json={
        "name": "", "url": "http://127.0.0.1:1/mcp",
    })
    assert r.status_code == 422


def test_register_same_name_upserts(client: TestClient, db_engine: AsyncEngine) -> None:
    client.post("/api/v1/mcp-servers", json={
        "name": "mcp-test-2", "url": "http://127.0.0.1:1/mcp",
    })
    client.post("/api/v1/mcp-servers", json={
        "name": "mcp-test-2", "url": "http://127.0.0.1:2/mcp",
    })
    names = _db_server_names(db_engine)
    assert names == {"mcp-test-2"}  # upsert, no duplicate row


# ------------------------------------------------------------------- list
def test_list_mcp_servers_empty(client: TestClient) -> None:
    body = client.get("/api/v1/mcp-servers").json()
    assert body["ok"] is True and body["data"]["items"] == []


# ------------------------------------------------------------------ delete
def test_delete_mcp_server_200(client: TestClient, db_engine: AsyncEngine) -> None:
    client.post("/api/v1/mcp-servers", json={
        "name": "mcp-test-3", "url": "http://127.0.0.1:1/mcp",
    })
    r = client.delete("/api/v1/mcp-servers/mcp-test-3")
    assert r.status_code == 200
    assert "mcp-test-3" not in _db_server_names(db_engine)
    names = [s["name"] for s in client.get("/api/v1/mcp-servers").json()["data"]["items"]]
    assert "mcp-test-3" not in names


def test_delete_mcp_server_404(client: TestClient) -> None:
    assert client.delete("/api/v1/mcp-servers/nope").status_code == 404


# ---------------------------------------------------- endpoint enable toggle
def test_patch_endpoint_enabled_toggle(client: TestClient) -> None:
    client.put("/api/v1/endpoints", json={
        "name": "his", "base_url": "https://his.example", "api_key": "h1",
        "auth_type": "header", "api_header": "X-HIS-Key",
    })
    auth = {"X-API-Key": "h1"}  # registering a keyed endpoint arms inbound auth
    # disable
    r = client.patch("/api/v1/endpoints/his", json={"enabled": False}, headers=auth)
    assert r.status_code == 200 and r.json()["data"]["enabled"] is False
    # re-enable
    r = client.patch("/api/v1/endpoints/his", json={"enabled": True}, headers=auth)
    assert r.status_code == 200 and r.json()["data"]["enabled"] is True


def test_patch_endpoint_unknown_404(client: TestClient) -> None:
    assert client.patch("/api/v1/endpoints/nope", json={"enabled": True}).status_code == 404


# ------------------------------------------------------- endpoint real delete
def test_delete_endpoint_removes_row(client: TestClient) -> None:
    client.put("/api/v1/endpoints", json={
        "name": "todel", "base_url": "https://x.example", "api_key": "kd1",
    })
    r = client.delete("/api/v1/endpoints/todel", headers={"X-API-Key": "kd1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    names = {e["name"] for e in client.get("/api/v1/endpoints").json()["endpoints"]}
    assert "todel" not in names


def test_delete_endpoint_unknown_404(client: TestClient) -> None:
    assert client.delete("/api/v1/endpoints/nope").status_code == 404


def test_delete_last_keyed_endpoint_reopens(client: TestClient) -> None:
    client.put("/api/v1/endpoints", json={
        "name": "armed", "base_url": "https://x.example", "api_key": "kd2",
    })
    # armed: inbound auth now requires the key
    assert client.get("/api/v1/agents/status").status_code == 401
    r = client.delete("/api/v1/endpoints/armed", headers={"X-API-Key": "kd2"})
    assert r.status_code == 200
    # last keyed endpoint gone -> open mode again
    assert client.get("/api/v1/agents/status").status_code == 200
