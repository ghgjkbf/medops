"""P2.5: external API onboarding tests (endpoint registry / LLM merge /
outbound client / inbound key auth / app wiring)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from medops_core.api_registry import (
    EndpointRegistry,
    ExternalApiClient,
    _sync_url,
    llm_providers_from_db,
    make_api_key_auth,
)
from medops_core.models import Base
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


@pytest.fixture()
async def registry(db_engine: AsyncEngine) -> EndpointRegistry:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return EndpointRegistry(async_sessionmaker(db_engine, expire_on_commit=False))


# ------------------------------------------------------------------ registry
async def test_upsert_and_list(registry: EndpointRegistry) -> None:
    await registry.upsert(
        "his-api", "https://his.example.com/api", "secret-his",
        auth_type="header", api_header="X-HIS-Key",
    )
    await registry.upsert(
        "deepseek-db", "https://api.deepseek.com/v1", "sk-db",
        kind="llm", model="deepseek-chat",
    )
    eps = await registry.list_endpoints(enabled_only=False)
    assert {e["name"] for e in eps} == {"his-api", "deepseek-db"}
    # upsert same name updates, not duplicates
    await registry.upsert("his-api", "https://his2.example.com", "k2")
    eps = await registry.list_endpoints(enabled_only=False)
    assert len(eps) == 2
    his = [e for e in eps if e["name"] == "his-api"][0]
    assert his["base_url"] == "https://his2.example.com"


async def test_enabled_filter(registry: EndpointRegistry) -> None:
    await registry.upsert("a", "https://a", enabled=True)
    await registry.upsert("b", "https://b", enabled=False)
    assert {e["name"] for e in await registry.list_endpoints()} == {"a"}
    await registry.set_enabled("b", True)
    assert len(await registry.list_endpoints()) == 2


def test_llm_providers_from_db(db_engine: AsyncEngine) -> None:
    from medops_core.models import ApiEndpoint
    from sqlalchemy.orm import sessionmaker

    engine = sync_create_engine(_sync_url(str(db_engine.url)))
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        s.add(ApiEndpoint(name="db-llm", base_url="https://llm.example.com/v1",
                          api_key="sk-1", kind="llm", model="test-model"))
        s.add(ApiEndpoint(name="generic", base_url="https://x", api_key="k",
                          kind="generic"))
        s.commit()

    providers = llm_providers_from_db(str(db_engine.url))
    names = [p.name for p in providers]
    assert "db-llm" in names and "generic" not in names
    llm = [p for p in providers if p.name == "db-llm"][0]
    assert llm.model == "test-model" and llm.api_key == "sk-1"
    engine.dispose()


# ------------------------------------------------------------- external call
async def test_external_client_bearer(registry: EndpointRegistry,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    await registry.upsert("httpbin", "https://httpbin.example", "tok-1")
    client = ExternalApiClient(registry)

    captured: dict = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, timeout: float | None = None) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(self, method: str, url: str, headers: dict | None = None,
                          json: dict | None = None) -> FakeResp:
            captured.update(method=method, url=url, headers=headers or {})
            return FakeResp()

    import medops_core.api_registry as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeAsyncClient)
    r = await client.call("httpbin", "GET", "status")
    assert r["status"] == 200 and r["body"] == {"ok": True}
    assert captured["headers"]["Authorization"] == "Bearer tok-1"
    assert captured["url"] == "https://httpbin.example/status"


async def test_external_client_custom_header(registry: EndpointRegistry) -> None:
    await registry.upsert("his", "https://his.example", "h1",
                          auth_type="header", api_header="X-HIS-Key")
    client = ExternalApiClient(registry)
    headers = client._headers((await registry.list_endpoints())[0])  # noqa: SLF001
    assert headers == {"X-HIS-Key": "h1"}


async def test_external_client_unknown_endpoint(registry: EndpointRegistry) -> None:
    client = ExternalApiClient(registry)
    r = await client.call("nope", "GET", "")
    assert r["status"] == 0 and "not registered" in r["error"]


# ----------------------------------------------------------------- inbound
async def test_api_key_auth_open_mode(db_engine: AsyncEngine) -> None:
    """No keys registered -> dependency passes (open mode)."""
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    dep = make_api_key_auth(async_sessionmaker(db_engine, expire_on_commit=False))
    await dependency_call(dep, None)  # no raise


async def dependency_call(dep, x_api_key: str | None) -> None:  # noqa: ANN001
    await dep(x_api_key=x_api_key)


async def test_api_key_auth_enforced(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    reg = EndpointRegistry(factory)
    await reg.upsert("gw", "https://x", "valid-key-1")
    await reg.upsert("gw2", "https://y", "valid-key-2")
    dep = make_api_key_auth(factory)

    await dep(x_api_key="valid-key-1")  # ok
    await dep(x_api_key="valid-key-2")  # ok
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc401:
        await dep(x_api_key=None)
    assert exc401.value.status_code == 401
    with pytest.raises(HTTPException) as exc403:
        await dep(x_api_key="wrong")
    assert exc403.value.status_code == 403


# --------------------------------------------------------------- app wiring
def test_app_endpoints_crud(db_engine: AsyncEngine) -> None:
    """Whole test runs on TestClient's loop only: sync DDL + a dedicated
    NullPool async engine so no connection is shared across event loops."""
    from medops_core.app import create_app
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    sync_engine = sync_create_engine(_sync_url(str(db_engine.url)))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    application = create_app()
    async_engine = create_async_engine(str(db_engine.url), poolclass=NullPool)
    application.state.endpoint_registry = EndpointRegistry(
        async_sessionmaker(async_engine, expire_on_commit=False)
    )
    application.state.external_api = ExternalApiClient(
        application.state.endpoint_registry
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

    # open mode gone: /api/v1/* now requires the key
    r401 = client.post("/api/v1/chat", json={"message": "hi"})
    assert r401.status_code == 401
    r403 = client.post(
        "/api/v1/chat", json={"message": "hi"}, headers={"X-API-Key": "wrong"}
    )
    assert r403.status_code == 403
    r_ok = client.post(
        "/api/v1/chat", json={"message": "hi"}, headers={"X-API-Key": "h1"}
    )
    assert r_ok.status_code == 200
