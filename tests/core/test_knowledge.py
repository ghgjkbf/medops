"""P4c: knowledge base — 2-gram scored retrieval, REST API, file import,
idempotent builtin seed (fault cause & handling docs for the 9 scenarios)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from medops_core import knowledge
from medops_core.app import create_app
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import Base
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture()
def factory(db_engine: AsyncEngine) -> async_sessionmaker:
    sync_engine = sync_create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    return async_sessionmaker(
        create_async_engine(
            db_engine.url.render_as_string(hide_password=False), poolclass=NullPool
        ),
        expire_on_commit=False,
    )


@pytest.fixture()
def client(db_engine: AsyncEngine) -> TestClient:
    sync_engine = sync_create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    application = create_app()
    engine = create_async_engine(
            db_engine.url.render_as_string(hide_password=False), poolclass=NullPool
        )
    application.state.db_factory = async_sessionmaker(engine, expire_on_commit=False)
    application.state.endpoint_registry = _StubRegistry()
    yield TestClient(application)
    engine.dispose()


class _StubRegistry:
    handles: list = []

    def list_status(self) -> list[dict]:
        return []


# ------------------------------------------------------------------ scoring
def test_terms_chinese_bigrams_and_ascii() -> None:
    terms = knowledge._terms("球管过热 tube")
    assert "球管" in terms and "管过" in terms and "tube" in terms


def test_score_title_weighted_over_body() -> None:
    hi = knowledge.score_doc("球管", "球管过热处理", "无关内容")
    lo = knowledge.score_doc("球管", "其他文档", "提到球管一次")
    assert hi > lo > 0


# ------------------------------------------------------------ module CRUD
async def test_add_and_search_ranking(factory: async_sessionmaker) -> None:
    await knowledge.add_document(factory, "CT 球管过热处理", "检查冷却水流量，清洗滤网")
    await knowledge.add_document(factory, "呼吸机管路泄漏", "逐段检查管路连接")
    hits = await knowledge.search_documents(factory, "球管过热怎么处理")
    assert hits and hits[0]["title"] == "CT 球管过热处理"
    assert hits[0]["score"] > 0


async def test_search_no_hit_returns_empty(factory: async_sessionmaker) -> None:
    await knowledge.add_document(factory, "无关文档", "内容")
    assert await knowledge.search_documents(factory, "量子力学") == []


async def test_delete_document(factory: async_sessionmaker) -> None:
    doc_id = await knowledge.add_document(factory, "待删", "内容")
    assert await knowledge.delete_document(factory, doc_id) is True
    assert await knowledge.delete_document(factory, doc_id) is False


# ------------------------------------------------------------ builtin seed
async def test_seed_builtin_idempotent(factory: async_sessionmaker) -> None:
    n1 = await knowledge.seed_builtin(factory)
    n2 = await knowledge.seed_builtin(factory)
    assert n1 >= 9  # 9 fault scenarios covered
    assert n2 == 0  # second run inserts nothing


async def test_seed_covers_all_scenarios(factory: async_sessionmaker) -> None:
    await knowledge.seed_builtin(factory)
    titles = {d["title"] for d in await knowledge.list_documents(factory)}
    for scenario in ("tube_overheat", "ventilator_leak", "disk_full", "ecg_lead_off"):
        assert any(scenario in t for t in titles)


# ----------------------------------------------------------------- REST API
def test_api_add_list_delete(client: TestClient) -> None:
    r = client.post("/api/v1/knowledge", json={
        "title": "CT 球管过热处理", "content": "检查冷却水流", "device_type": "ct",
    })
    assert r.status_code == 201 and r.json()["data"]["id"] >= 1
    body = client.get("/api/v1/knowledge").json()["data"]
    assert body["count"] == 1 and body["items"][0]["title"] == "CT 球管过热处理"
    doc_id = body["items"][0]["id"]
    assert client.delete(f"/api/v1/knowledge/{doc_id}").status_code == 200
    assert client.get("/api/v1/knowledge").json()["data"]["count"] == 0


def test_api_search_endpoint(client: TestClient) -> None:
    client.post(
        "/api/v1/knowledge",
        json={"title": "氧电池漂移处理", "content": "更换氧电池并定标"},
    )
    hits = client.get("/api/v1/knowledge", params={"q": "氧电池漂移"}).json()["data"]["items"]
    assert hits and "氧电池" in hits[0]["title"]


def test_api_add_empty_title_422(client: TestClient) -> None:
    assert client.post("/api/v1/knowledge", json={"title": "", "content": "x"}).status_code == 422


def test_api_delete_unknown_404(client: TestClient) -> None:
    assert client.delete("/api/v1/knowledge/999").status_code == 404


def test_api_import_txt_file(client: TestClient) -> None:
    content = "DR 磁盘写满：先归档旧检查到 PACS，再清理临时目录。"
    r = client.post(
        "/api/v1/knowledge/import",
        files={"file": ("dr_disk_full.md", content.encode())},
    )
    assert r.status_code == 201
    assert r.json()["data"]["title"] == "dr_disk_full.md"
    hits = client.get("/api/v1/knowledge", params={"q": "磁盘写满"}).json()["data"]["items"]
    assert hits and "PACS" in hits[0]["content"]


def test_api_import_oversize_422(client: TestClient) -> None:
    big = b"x" * (512 * 1024 + 1)
    r = client.post("/api/v1/knowledge/import", files={"file": ("big.txt", big)})
    assert r.status_code == 422
