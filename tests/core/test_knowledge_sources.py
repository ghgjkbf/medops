"""P5c: external knowledge sources — web/rss/vector_store binding, SSRF
guard, sha256 dedupe, chunked ingestion, due-scan scheduling, REST, butler."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from medops_core import knowledge, sources
from medops_core.agents.butler import ButlerAgent
from medops_core.app import create_app
from medops_core.mcp_client.registry import MCPRegistry
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import Base, KnowledgeSource
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


@pytest.fixture(autouse=True)
def _vecdb(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setenv("MEDOPS_KB_VECDB", str(tmp_path / "knowledge.vecdb"))


async def _doc_titles(factory: async_sessionmaker) -> list[str]:
    return [d["title"] for d in await knowledge.list_documents(factory)]


# ------------------------------------------------------------------- CRUD
async def test_add_list_delete_source(factory: async_sessionmaker) -> None:
    sid = await sources.add_source(factory, "wiki", "web", "https://wiki.example/kb")
    rows = await sources.list_sources(factory)
    assert len(rows) == 1 and rows[0]["name"] == "wiki" and rows[0]["status"] == "idle"
    assert await sources.delete_source(factory, sid) is True
    assert await sources.list_sources(factory) == []


async def test_duplicate_name_rejected(factory: async_sessionmaker) -> None:
    await sources.add_source(factory, "dup", "web", "https://a.example")
    with pytest.raises(sources.SourceExists):
        await sources.add_source(factory, "dup", "rss", "https://b.example")


async def test_unknown_type_rejected(factory: async_sessionmaker) -> None:
    with pytest.raises(ValueError, match="type"):
        await sources.add_source(factory, "bad", "ftp", "ftp://x")


# ------------------------------------------------------------------- sync
async def test_web_sync_strips_tags_and_chunks(factory: async_sessionmaker) -> None:
    sid = await sources.add_source(factory, "wiki", "web", "https://w.example/page")
    html = (
        "<html><head><style>body{}</style></head><body>"
        "<h1>CT 维护手册</h1><p>" + "球管保养要点。" * 60 + "</p>"
        "<p>" + "探测器校准说明。" * 60 + "</p></body></html>"
    )
    result = await sources.sync_source(factory, sid, fetcher=lambda url: html)
    assert result["added"] >= 2  # chunked
    titles = await _doc_titles(factory)
    assert any("wiki" in t for t in titles)
    assert all("<" not in t for t in await _fetch_all_contents(factory))


async def _fetch_all_contents(factory: async_sessionmaker) -> list[str]:
    return [d["content"] for d in await knowledge.list_documents(factory)]


async def test_web_sync_dedupes_second_run(factory: async_sessionmaker) -> None:
    sid = await sources.add_source(factory, "wiki", "web", "https://w.example/page")
    html = "<p>" + "重复内容测试。" * 100 + "</p>"
    first = await sources.sync_source(factory, sid, fetcher=lambda url: html)
    second = await sources.sync_source(factory, sid, fetcher=lambda url: html)
    assert first["added"] >= 1 and second["added"] == 0


async def test_sync_updates_status(factory: async_sessionmaker) -> None:
    sid = await sources.add_source(factory, "ok-src", "web", "https://w.example")
    await sources.sync_source(factory, sid, fetcher=lambda url: "<p>内容</p>")
    row = (await sources.list_sources(factory))[0]
    assert row["status"] == "ok" and row["last_synced_at"] is not None


async def test_sync_fetch_error_marks_source_error(factory: async_sessionmaker) -> None:
    sid = await sources.add_source(factory, "bad-src", "web", "https://w.example")

    def boom(url: str) -> str:
        raise RuntimeError("network down")

    result = await sources.sync_source(factory, sid, fetcher=boom)
    assert result["ok"] is False
    row = (await sources.list_sources(factory))[0]
    assert row["status"] == "error" and "network down" in row["meta"].get("last_error", "")


# ------------------------------------------------------------------- SSRF
async def test_ssrf_private_address_blocked(factory: async_sessionmaker) -> None:
    sid = await sources.add_source(factory, "intranet", "web", "http://127.0.0.1:8123/secret")
    result = await sources.sync_source(factory, sid)  # no fetcher -> real fetch path
    assert result["ok"] is False and "private" in result["error"]


async def test_ssrf_allowlist_bypasses_ip_check(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEDOPS_KB_ALLOW_HOSTS", "127.0.0.1")
    sid = await sources.add_source(factory, "local", "web", "http://127.0.0.1:9/x")
    # allowlisted: passes the IP check; the (mocked) fetch then decides
    result = await sources.sync_source(factory, sid, fetcher=lambda url: "<p>ok</p>")
    assert result["ok"] is True


async def test_oversize_content_rejected(factory: async_sessionmaker) -> None:
    sid = await sources.add_source(factory, "big", "web", "https://w.example")
    result = await sources.sync_source(factory, sid, fetcher=lambda url: "x" * (1024 * 1024 + 1))
    assert result["ok"] is False and "too large" in result["error"]


# -------------------------------------------------------------------- rss
async def test_rss_sync_parses_items(factory: async_sessionmaker) -> None:
    sid = await sources.add_source(factory, "bulletin", "rss", "https://w.example/rss")
    rss = (
        "<rss><channel>"
        "<item><title>CT 召回公告</title><description>某型号球管召回。</description></item>"
        "<item><title>维保通知</title><description>季度保养开始。</description></item>"
        "</channel></rss>"
    )
    result = await sources.sync_source(factory, sid, fetcher=lambda url: rss)
    assert result["added"] == 2
    titles = await _doc_titles(factory)
    assert any("CT 召回公告" in t for t in titles)


# ------------------------------------------------------------ vector_store
async def test_vector_store_import(factory: async_sessionmaker, tmp_path) -> None:  # noqa: ANN001
    db = tmp_path / "external.vecdb"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE docs (id INTEGER PRIMARY KEY, title TEXT, content TEXT)")
    conn.execute("INSERT INTO docs VALUES (1, '外部知识一', '外部故障处理内容')")
    conn.commit()
    conn.close()

    sid = await sources.add_source(factory, "vec", "vector_store", str(db))
    result = await sources.sync_source(factory, sid)
    assert result["added"] == 1
    titles = await _doc_titles(factory)
    assert any("外部知识一" in t for t in titles)


async def test_vector_store_missing_file_errors(factory: async_sessionmaker, tmp_path) -> None:  # noqa: ANN001
    sid = await sources.add_source(factory, "ghost", "vector_store", str(tmp_path / "no.vecdb"))
    result = await sources.sync_source(factory, sid)
    assert result["ok"] is False


# ------------------------------------------------------------- due scan
def test_is_due_logic() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    manual = KnowledgeSource(name="m", type="web", url="u", schedule_minutes=None)
    assert sources._is_due(manual, now) is False
    due = KnowledgeSource(name="d", type="web", url="u", schedule_minutes=30,
                          last_synced_at=now - timedelta(minutes=31))
    assert sources._is_due(due, now) is True
    fresh = KnowledgeSource(name="f", type="web", url="u", schedule_minutes=30,
                            last_synced_at=now - timedelta(minutes=5))
    assert sources._is_due(fresh, now) is False
    never = KnowledgeSource(name="n", type="web", url="u", schedule_minutes=60)
    assert sources._is_due(never, now) is True


# -------------------------------------------------------------------- REST
def test_rest_sources_crud_and_sync(db_engine: AsyncEngine, tmp_path) -> None:  # noqa: ANN001
    sync_engine = sync_create_engine(_sync_url(db_engine.url.render_as_string(hide_password=False)))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    db = tmp_path / "ext.vecdb"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE docs (id INTEGER PRIMARY KEY, title TEXT, content TEXT)")
    conn.execute("INSERT INTO docs VALUES (1, 'REST 外部知识', '内容')")
    conn.commit()
    conn.close()

    application = create_app()
    engine = create_async_engine(
            db_engine.url.render_as_string(hide_password=False), poolclass=NullPool
        )
    application.state.db_factory = async_sessionmaker(engine, expire_on_commit=False)
    application.state.endpoint_registry = _StubRegistry()
    client = TestClient(application)

    r = client.post("/api/v1/knowledge-sources", json={
        "name": "ext", "type": "vector_store", "url": str(db)})
    assert r.status_code == 201
    sid = r.json()["data"]["id"]
    sync = client.post(f"/api/v1/knowledge-sources/{sid}/sync").json()["data"]
    assert sync["ok"] is True and sync["added"] == 1
    rows = client.get("/api/v1/knowledge-sources").json()["data"]["items"]
    assert rows[0]["status"] == "ok"
    assert client.delete(f"/api/v1/knowledge-sources/{sid}").status_code == 200
    engine.dispose()


class _StubRegistry:
    handles: list = []

    def list_status(self) -> list[dict]:
        return []


# ------------------------------------------------------------------ butler
async def test_butler_knowledge_source_tools(factory: async_sessionmaker, tmp_path) -> None:  # noqa: ANN001
    db = tmp_path / "ext2.vecdb"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE docs (id INTEGER PRIMARY KEY, title TEXT, content TEXT)")
    conn.execute("INSERT INTO docs VALUES (1, '管家导入知识', '内容')")
    conn.commit()
    conn.close()

    await sources.add_source(factory, "src1", "vector_store", str(db))
    butler = ButlerAgent(db_factory=factory, registry=MCPRegistry())
    result = await butler.execute_task("同步知识源 src1")
    assert result["status"] == "executed", result
    assert result["result"]["ok"] is True, result["result"]
    listing = await butler.execute_task("列出知识源")
    assert listing["result"]["ok"] is True and listing["result"]["count"] == 1
