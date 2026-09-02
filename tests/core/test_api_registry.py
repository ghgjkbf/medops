"""P2.5: external API onboarding tests (endpoint registry / LLM merge /
outbound client / inbound key auth)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from medops_core.api_registry import (
    EndpointRegistry,
    ExternalApiClient,
    llm_providers_from_db,
    make_api_key_auth,
)
from medops_core.models import Base
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture()
def sync(db_engine: AsyncEngine) -> EndpointRegistry:
    import asyncio

    async def _create():
        async with db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_create())
    from medops_core.mcp_client.sync import _sync_url
    from sqlalchemy import create_engine as ce
    from sqlalchemy.orm import sessionmaker

    url = _sync_url(str(db_engine.url))

    return EndpointRegistry(sessionmaker(bind=ce(url), expire_on_commit=False))


# ------------------------------------------------------------------ registry
def test_upsert_and_list(sync: EndpointRegistry) -> None:
    sync.upsert("his-api", "https://his.example.com/api", "secret-his",
                auth_type="header", api_header="X-HIS-Key")
    sync.upsert("deepseek-db", "https://api.deepseek.com/v1", "sk-db",
                kind="llm", model="deepseek-chat")
    eps = sync.list_endpoints()
    assert {e["name"] for e in eps} == {"his-api", "deepseek-db"}
    # upsert same name updates, not duplicates
    sync.upsert("his-api", "https://his2.example.com", "k2")
    assert len(sync.list_endpoints()) == 2
    his = [e for e in sync.list_endpoints() if e["name"] == "his-api"][0]
    assert his["base_url"] == "https://his2.example.com"


def test_enabled_filter(sync: EndpointRegistry) -> None:
    sync.upsert("a", "https://a", enabled=True)
    sync.upsert("b", "https://b", enabled=False)
    assert {e["name"] for e in sync.list_endpoints(enabled_only=True)} == {"a"}
    sync.set_enabled("b", True)
    assert len(sync.list_endpoints(enabled_only=True)) == 2


def test_llm_providers_from_db(sync: EndpointRegistry) -> None:
    sync.upsert("db-llm", "https://llm.example.com/v1", "sk-1",
                kind="llm", model="test-model")
    sync.upsert("generic", "https://x", "k", kind="generic")
    providers = llm_providers_from_db(sync)
    assert len(providers) == 1
    assert providers[0].name == "db-llm"
    assert providers[0].model == "test-model"


# ------------------------------------------------------------- external call
def test_external_client_bearer(sync: EndpointRegistry) -> None:
    sync.upsert("httpbin", "https://httpbin.example", "tok-1")
    client = ExternalApiClient(sync)

    captured = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        captured["headers"] = headers
        return type("R", (), {
            "status_code": 200,
            "json": staticmethod(lambda: {"ok": True}),
            "text": "",
        })()

    import medops_core.api_registry as mod
    original = mod.httpx.request
    mod.httpx.request = fake_request
    try:
        r = client.call("httpbin", "GET", "status")
    finally:
        mod.httpx.request = original
    assert r["status"] == 200 and r["body"] == {"ok": True}
    assert captured["headers"]["Authorization"] == "Bearer tok-1"
    assert captured["url"] == "https://httpbin.example/status"


def test_external_client_unknown_endpoint(sync: EndpointRegistry) -> None:
    client = ExternalApiClient(sync)
    r = client.call("nope", "GET", "")
    assert r["status"] == 0 and "not registered" in r["error"]


# ----------------------------------------------------------------- inbound
def test_api_key_auth_open_mode(sync: EndpointRegistry) -> None:
    """No keys registered -> dependency passes (open mode)."""
    dep = make_api_key_auth(sync._session_factory)  # noqa: SLF001
    dep(x_api_key=None)  # no raise


def test_api_key_auth_enforced(sync: EndpointRegistry) -> None:
    sync.upsert("gateway", "https://x", "valid-key-1")
    sync.upsert("gateway2", "https://y", "valid-key-2")
    dep = make_api_key_auth(sync._session_factory)  # noqa: SLF001

    dep(x_api_key="valid-key-1")  # ok
    dep(x_api_key="valid-key-2")  # ok
    with pytest.raises(Exception, match="required"):
        dep(x_api_key=None)
    with pytest.raises(Exception, match="invalid"):
        dep(x_api_key="wrong")


# --------------------------------------------------------------- app wiring
async def test_app_endpoints_crud(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from medops_core.app import create_app

    application = create_app()
    # point the app's endpoint registry at the test DB
    from medops_core.mcp_client.sync import _sync_url
    from sqlalchemy import create_engine as ce
    from sqlalchemy.orm import sessionmaker as sm

    application.state.endpoint_registry = EndpointRegistry(
        sm(bind=ce(_sync_url(str(db_engine.url))), expire_on_commit=False)
    )
    client = TestClient(application)
    # health always open
    assert client.get("/api/v1/health").status_code == 200

    # register an endpoint via API
    r = client.put("/api/v1/endpoints", json={
        "name": "his", "base_url": "https://his.example", "api_key": "h1",
        "auth_type": "header", "api_header": "X-HIS-Key",
    })
    assert r.status_code == 200 and r.json()["ok"] is True

    # list masks the key (auth now enforced; use the registered key)
    listing = client.get(
        "/api/v1/endpoints", headers={"X-API-Key": "h1"}
    ).json()
    assert listing["endpoints"][0]["api_key"] == "***"

    # open mode still (gateway key exists -> now enforced for /api/v1/*)
    r401 = client.post("/api/v1/chat", json={"message": "hi"})
    assert r401.status_code == 401
    r_ok = client.post("/api/v1/chat", json={"message": "hi"},
                       headers={"X-API-Key": "h1"})
    assert r_ok.status_code == 200
