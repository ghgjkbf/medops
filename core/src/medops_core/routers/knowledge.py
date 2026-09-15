"""Knowledge base + knowledge-source routes.

- /api/v1/knowledge           docs CRUD + file import (P4c)
- /api/v1/knowledge-sources   external source binding / sync (P5c)
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile

from medops_core import knowledge, sources
from medops_core.schemas import KnowledgeIn, KnowledgeSourceIn

_KB_MAX_BYTES = 512 * 1024


def register(app: FastAPI) -> None:
    # ------------------------------------------------- knowledge base (P4c)
    @app.get("/api/v1/knowledge")
    async def knowledge_endpoint(q: str | None = None, limit: int = 8) -> dict:
        factory = app.state.db_factory
        if q:
            items = await knowledge.search_documents(factory, q, limit=limit)
        else:
            items = await knowledge.list_documents(factory)
        return {
            "ok": True,
            "data": {
                "count": len(items),
                "items": items,
                "backend": knowledge.active_backend(),
            },
        }

    @app.post("/api/v1/knowledge", status_code=201)
    async def knowledge_add(body: KnowledgeIn) -> dict:
        factory = app.state.db_factory
        meta: dict[str, Any] = {"source": "manual"}
        if body.device_type:
            meta["device_type"] = body.device_type
        doc_id = await knowledge.add_document(factory, body.title, body.content, meta)
        return {"ok": True, "data": {"id": doc_id, "title": body.title}}

    @app.post("/api/v1/knowledge/import", status_code=201)
    async def knowledge_import(file: UploadFile) -> dict:
        raw = await file.read()
        if len(raw) > _KB_MAX_BYTES:
            raise HTTPException(status_code=422, detail="file too large (max 512 KB)")
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            raise HTTPException(status_code=422, detail="file is empty")
        title = file.filename or "imported-doc"
        doc_id = await knowledge.add_document(
            app.state.db_factory,
            title,
            text,
            {"source": "upload", "filename": title},
        )
        return {
            "ok": True,
            "data": {"id": doc_id, "title": title, "chars": len(text)},
        }

    @app.delete("/api/v1/knowledge/{doc_id}")
    async def knowledge_delete(doc_id: int) -> dict:
        if not await knowledge.delete_document(app.state.db_factory, doc_id):
            raise HTTPException(status_code=404, detail="knowledge doc not found")
        return {"ok": True, "data": {"deleted": doc_id}}

    # ---------------------------------------------- knowledge sources (P5c)
    @app.get("/api/v1/knowledge-sources")
    async def ks_list() -> dict:
        rows = await sources.list_sources(app.state.db_factory)
        return {"ok": True, "data": {"count": len(rows), "items": rows}}

    @app.post("/api/v1/knowledge-sources", status_code=201)
    async def ks_add(body: KnowledgeSourceIn) -> dict:
        try:
            source_id = await sources.add_source(
                app.state.db_factory,
                body.name,
                body.type,
                body.url,
                body.schedule_minutes,
            )
        except sources.SourceExists as exc:
            raise HTTPException(
                status_code=409, detail="source name already exists"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "data": {"id": source_id, "name": body.name}}

    @app.delete("/api/v1/knowledge-sources/{source_id}")
    async def ks_delete(source_id: int) -> dict:
        if not await sources.delete_source(app.state.db_factory, source_id):
            raise HTTPException(status_code=404, detail="knowledge source not found")
        return {"ok": True, "data": {"deleted": source_id}}

    @app.post("/api/v1/knowledge-sources/{source_id}/sync")
    async def ks_sync(source_id: int) -> dict:
        result = await sources.sync_source(app.state.db_factory, source_id)
        return {"ok": True, "data": result}
