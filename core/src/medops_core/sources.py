"""External knowledge sources (P5c): bind web pages / RSS / vector stores.

Ingestion pipeline: fetch (size-capped, SSRF-guarded) -> text extraction ->
chunk (~800 chars) -> sha256 dedupe -> knowledge_doc. Facts live in PG;
the vector index (sqlite-vec) is a rebuildable derivative.

Sync modes: manual (default) or scheduled via `schedule_minutes`
(lifespan task `sync_due` picks up due sources).
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import os
import re
import socket
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from medops_core import knowledge
from medops_core.models import KnowledgeDoc, KnowledgeSource

MAX_FETCH_BYTES = 1024 * 1024
CHUNK_CHARS = 800
_TYPES = ("web", "rss", "vector_store")


class SourceExists(Exception):
    """A source with the same name is already registered."""


# ------------------------------------------------------------------- CRUD
async def add_source(
    factory,  # noqa: ANN001 - async_sessionmaker
    name: str,
    type: str,  # noqa: A002 - column name
    url: str,
    schedule_minutes: int | None = None,
) -> int:
    if type not in _TYPES:
        raise ValueError(f"unsupported source type {type!r} (web|rss|vector_store)")
    async with factory() as s:
        exists = (
            await s.scalars(select(KnowledgeSource).where(KnowledgeSource.name == name))
        ).first()
        if exists is not None:
            raise SourceExists(name)
        row = KnowledgeSource(
            name=name, type=type, url=url,
            schedule_minutes=schedule_minutes, status="idle",
        )
        s.add(row)
        await s.commit()
        return row.id


def _row_dict(row: KnowledgeSource) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "type": row.type,
        "url": row.url,
        "schedule_minutes": row.schedule_minutes,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
        "status": row.status,
        "meta": row.meta,
    }


async def list_sources(factory) -> list[dict[str, Any]]:  # noqa: ANN001
    async with factory() as s:
        rows = (
            await s.scalars(select(KnowledgeSource).order_by(KnowledgeSource.name))
        ).all()
    return [_row_dict(r) for r in rows]


async def delete_source(factory, source_id: int) -> bool:  # noqa: ANN001
    async with factory() as s:
        row = (
            await s.scalars(select(KnowledgeSource).where(KnowledgeSource.id == source_id))
        ).first()
        if row is None:
            return False
        await s.delete(row)
        await s.commit()
        return True


# -------------------------------------------------------------- scheduling
def _is_due(row: KnowledgeSource, now: datetime) -> bool:
    if row.schedule_minutes is None:
        return False
    if row.last_synced_at is None:
        return True
    return (now - row.last_synced_at).total_seconds() >= row.schedule_minutes * 60


async def sync_due(factory) -> list[dict[str, Any]]:  # noqa: ANN001
    """Sync every scheduled source that is due; returns per-source results."""
    now = datetime.now(UTC)
    results: list[dict[str, Any]] = []
    for row in await list_sources(factory):
        if _is_due_from_dict(row, now):
            results.append({"name": row["name"], **await sync_source(factory, row["id"])})
    return results


def _is_due_from_dict(row: dict[str, Any], now: datetime) -> bool:
    if row["schedule_minutes"] is None:
        return False
    if row["last_synced_at"] is None:
        return True
    synced = datetime.fromisoformat(row["last_synced_at"])
    return (now - synced).total_seconds() >= row["schedule_minutes"] * 60


# ------------------------------------------------------------------- fetch
def _guard_private_host(url: str) -> None:
    """SSRF guard: reject hosts that resolve to non-public addresses.

    Hosts listed in MEDOPS_KB_ALLOW_HOSTS (comma-separated) bypass the
    IP check (still only http/https, size-capped).
    """
    from urllib.parse import urlparse  # noqa: PLC0415

    host = urlparse(url).hostname or ""
    if not host:
        raise ValueError("blocked: URL has no host")
    allow = [h.strip() for h in os.environ.get("MEDOPS_KB_ALLOW_HOSTS", "").split(",") if h.strip()]
    if host in allow:
        return
    for info in socket.getaddrinfo(host, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"blocked: {host} resolves to a private address ({ip})")


async def _fetch_url(url: str) -> str:
    _guard_private_host(url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        if len(resp.content) > MAX_FETCH_BYTES:
            raise ValueError(f"content too large (>{MAX_FETCH_BYTES} bytes)")
        return resp.text


# -------------------------------------------------------------- extraction
def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n|。(?=\S)|\.(?=\s)", text) if p.strip()]
    out: list[str] = []
    cur = ""
    for part in parts:
        if len(cur) + len(part) + 1 <= size:
            cur = f"{cur} {part}".strip()
        else:
            if cur:
                out.append(cur)
            cur = part[:size]
    if cur:
        out.append(cur)
    return out or ([text[:size]] if text else [])


def _doc_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


async def _existing_hashes(factory) -> set[str]:  # noqa: ANN001
    async with factory() as s:
        rows = (await s.scalars(select(KnowledgeDoc))).all()
    return {(r.meta or {}).get("hash", "") for r in rows if r.meta}


async def _add_chunked(
    factory, source_id: int, source_type: str,  # noqa: ANN001
    base_title: str, text: str, existing: set[str],
) -> int:
    added = 0
    chunks = _chunks(text)
    for i, chunk in enumerate(chunks, 1):
        h = _doc_hash(chunk)
        if h in existing:
            continue
        title = base_title if len(chunks) == 1 else f"{base_title} #{i}"
        meta = {"source": source_type, "source_id": source_id, "hash": h}
        await knowledge.add_document(factory, title, chunk, meta)
        existing.add(h)
        added += 1
    return added


# -------------------------------------------------------------------- sync
async def sync_source(
    factory, source_id: int,  # noqa: ANN001
    fetcher: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Sync one source. `fetcher` (url -> body text) is injectable for tests."""
    async with factory() as s:
        row = (
            await s.scalars(select(KnowledgeSource).where(KnowledgeSource.id == source_id))
        ).first()
    if row is None:
        return {"ok": False, "error": "knowledge source not found"}

    status, err, added = "ok", "", 0
    try:
        existing = await _existing_hashes(factory)
        if row.type in ("web", "rss"):
            if fetcher is not None:
                text = fetcher(row.url)
            else:
                text = await _fetch_url(row.url)
            if len(text) > MAX_FETCH_BYTES:
                raise ValueError(f"content too large (>{MAX_FETCH_BYTES} bytes)")
            if row.type == "web":
                added = await _add_chunked(
                    factory, row.id, "web", row.name, _strip_html(text), existing)
            else:
                added = await _sync_rss(factory, row, text, existing)
        elif row.type == "vector_store":
            added = await _sync_vector_store(factory, row, existing)
    except Exception as exc:  # noqa: BLE001 - status lands on the row
        status, err, added = "error", f"{type(exc).__name__}: {exc}", 0

    async with factory() as s:
        db_row = (
            await s.scalars(select(KnowledgeSource).where(KnowledgeSource.id == source_id))
        ).first()
        if db_row is not None:
            db_row.status = status
            db_row.last_synced_at = datetime.now(UTC)
            meta = dict(db_row.meta or {})
            meta["last_error"] = err
            meta["last_added"] = added
            db_row.meta = meta
            await s.commit()
    if status != "ok":
        return {"ok": False, "error": err}
    return {"ok": True, "added": added}


async def _sync_rss(
    factory, row: KnowledgeSource, xml_text: str, existing: set[str]  # noqa: ANN001
) -> int:
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    root = ET.fromstring(xml_text)
    added = 0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip() or row.name
        desc = (item.findtext("description") or "").strip()
        if not desc:
            continue
        h = _doc_hash(desc)
        if h in existing:
            continue
        meta = {"source": "rss", "source_id": row.id, "hash": h}
        await knowledge.add_document(factory, title, desc, meta)
        existing.add(h)
        added += 1
    return added


async def _sync_vector_store(
    factory, row: KnowledgeSource, existing: set[str]  # noqa: ANN001
) -> int:
    """Import docs from another sqlite file with table docs(id,title,content)."""
    path = Path(row.url)
    if not path.exists():
        raise FileNotFoundError(f"vector store file not found: {path}")
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT id, title, content FROM docs").fetchall()
    finally:
        conn.close()
    added = 0
    for ext_id, title, content in rows:
        if not content:
            continue
        h = _doc_hash(content)
        if h in existing:
            continue
        meta = {"source": "vector_store", "source_id": row.id,
                "hash": h, "external_id": ext_id}
        await knowledge.add_document(factory, title or f"imported-{ext_id}", content, meta)
        existing.add(h)
        added += 1
    return added
