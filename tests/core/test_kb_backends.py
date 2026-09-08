"""P5b: three-tier retrieval — keyword (jieba+bm25s, new default) / vector
(fastembed+sqlite-vec, stub-embedder tested) / legacy (2-gram, kept)."""

from __future__ import annotations

import pytest
from medops_core import knowledge
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import Base
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

_GOLDEN = [
    ("球管过热怎么处理", "CT 球管过热"),
    ("氧电池漂移了怎么办", "呼吸机氧电池漂移"),
    ("呼吸机管路漏气了", "呼吸机管路泄漏"),
    ("DR 磁盘快满了", "DR 磁盘写满"),
    ("发生器过热了", "DR 发生器过热"),
    ("探测器制冷出问题", "DR 探测器制冷衰减"),
    ("心电导联脱落怎么办", "心电导联脱落"),
    ("电池低电量告警", "心电电池低电量"),
    ("PACS 一直连不上", "CT PACS 断连"),
    ("tube overheat", "CT 球管过热"),
]


@pytest.fixture()
def factory(db_engine: AsyncEngine) -> async_sessionmaker:
    sync_engine = sync_create_engine(_sync_url(str(db_engine.url)))
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    return async_sessionmaker(
        create_async_engine(str(db_engine.url), poolclass=NullPool),
        expire_on_commit=False,
    )


@pytest.fixture()
async def seeded(factory: async_sessionmaker) -> async_sessionmaker:
    await knowledge.seed_builtin(factory)
    return factory


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.delenv("MEDOPS_KB_BACKEND", raising=False)
    monkeypatch.setenv("MEDOPS_KB_VECDB", str(tmp_path / "knowledge.vecdb"))
    monkeypatch.delenv("MEDOPS_KB_DISABLE_AUTO_VEC", raising=False)


# ----------------------------------------------------------- golden keyword
@pytest.mark.parametrize(("query", "expected_in"), _GOLDEN)
async def test_keyword_golden_hits(seeded: async_sessionmaker,
                                   query: str, expected_in: str) -> None:
    hits = await knowledge.search_documents(seeded, query, limit=3)
    titles = [h["title"] for h in hits]
    assert hits, f"{query}: no hits at all"
    assert any(expected_in in t for t in titles), f"{query}: {titles}"


async def test_keyword_ranks_relevant_first(seeded: async_sessionmaker) -> None:
    hits = await knowledge.search_documents(seeded, "球管", limit=3)
    assert "球管" in hits[0]["title"]


# ---------------------------------------------------------------- backends
async def test_default_backend_is_keyword() -> None:
    assert knowledge.active_backend() == "keyword"


async def test_env_backend_legacy(seeded: async_sessionmaker,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDOPS_KB_BACKEND", "legacy")
    assert knowledge.active_backend() == "legacy"
    hits = await knowledge.search_documents(seeded, "球管过热怎么处理", limit=3)
    assert hits and "球管" in hits[0]["title"]


async def test_keyword_beats_legacy_on_golden(seeded: async_sessionmaker) -> None:
    """keyword must not lose any golden case that legacy passes (and wins
    where BM25's IDF helps)."""
    legacy_miss = 0
    for query, expected_in in _GOLDEN:
        legacy = await knowledge._search_legacy(seeded, query, limit=3)
        if not any(expected_in in h["title"] for h in legacy):
            legacy_miss += 1
    kw_miss = 0
    for query, expected_in in _GOLDEN:
        kw = await knowledge._search_keyword(seeded, query, limit=3)
        if not any(expected_in in h["title"] for h in kw):
            kw_miss += 1
    assert kw_miss <= legacy_miss


# ------------------------------------------------------------- legacy kept
async def test_legacy_returns_same_shape(seeded: async_sessionmaker) -> None:
    hits = await knowledge._search_legacy(seeded, "球管过热", limit=2)
    assert hits and set(hits[0]) >= {"id", "title", "content", "score"}


# ------------------------------------------------------ vector: fallback+stub
async def test_vector_unavailable_falls_back_to_keyword(
    seeded: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEDOPS_KB_BACKEND", "vector")
    monkeypatch.setattr(knowledge, "_embed_text",
                        lambda t: (_ for _ in ()).throw(RuntimeError("no model")))
    hits = await knowledge.search_documents(seeded, "球管过热怎么处理", limit=3)
    assert hits and any("球管" in h["title"] for h in hits)


def _stub_embed(text: str) -> list[float]:
    """Deterministic char-bag embedding (512-d): shared CJK chars -> similar."""
    vec = [0.0] * 512
    for ch in text:
        if ch.strip():
            vec[hash(ch) % 512] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


async def test_vector_roundtrip_with_stub_embeddings(
    seeded: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge, "_embed_text", _stub_embed)
    n = await knowledge.reindex_vector(seeded)
    assert n >= 9
    # query "球管" shares chars with the CT doc title the most
    hits = await knowledge._search_vector(seeded, "球管过热怎么处理", limit=3)
    assert hits and any("球管" in h["title"] for h in hits)
    assert hits[0]["score"] > 0  # similarity score present


async def test_vector_stale_index_auto_reindexes(
    seeded: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge, "_embed_text", _stub_embed)
    await knowledge.reindex_vector(seeded)
    await knowledge.add_document(seeded, "新的文档", "新内容占位")
    # stale (11 vs 10 in vecdb) -> auto reindex on search
    hits = await knowledge._search_vector(seeded, "新内容", limit=3)
    assert any("新的文档" in h["title"] for h in hits)


async def test_keyword_tokenizes_underscores() -> None:
    terms = knowledge._tokenize("tube_temp=64.75 球管过热")
    assert "tube" in terms and "temp" in terms and "球管" in terms
