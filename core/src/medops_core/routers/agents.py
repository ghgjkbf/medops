"""Chat + butler routes.

- POST /api/v1/chat          secretary Q&A (trajectory included)
- POST /api/v1/butler/task   management operation (delegated to the butler)
- GET  /api/v1/butler/audit  operation audit trail
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from medops_core.agents.llm import rule_based_fallback
from medops_core.agents.secretary import SecretaryAgent
from medops_core.bootstrap import build_llm
from medops_core.db import async_session_factory
from medops_core.models import ButlerAudit, ChatMessage, ChatSession
from medops_core.schemas import ButlerTaskIn

_LOG = logging.getLogger("medops")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


async def _ask_secretary(app: FastAPI, message: str) -> tuple[str, list[dict], str]:
    """Run the secretary (or rule fallback) and return (answer, trajectory, provider)."""
    if not app.state.registry.handles:
        return rule_based_fallback(message), [], "rules"
    llm = getattr(app.state, "llm", None) or build_llm()
    agent = SecretaryAgent(
        llm,
        app.state.registry,
        session=None,
        db_factory=app.state.db_factory,
        butler=app.state.butler,
        inspector=app.state.inspector,
    )
    result = await agent.run(message)
    return result.answer, result.trajectory_dicts(), result.provider_used


async def _persist_chat(session_key: str, message: str, answer: str,
                        trajectory: list[dict]) -> None:
    """Best-effort chat persistence (DB may be absent in degraded mode)."""
    try:
        async with async_session_factory() as session:
            chat = ChatSession(session_key=session_key)
            session.add(chat)
            await session.flush()
            session.add(ChatMessage(session_id=chat.id, role="user", content=message))
            session.add(
                ChatMessage(
                    session_id=chat.id,
                    role="assistant",
                    content=answer,
                    tool_trace=trajectory,
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - persistence is best-effort
        _LOG.warning("chat persistence failed", exc_info=True)


def register(app: FastAPI) -> None:
    @app.post("/api/v1/chat")
    async def chat(req: ChatRequest) -> dict:
        answer, trajectory, provider = await _ask_secretary(app, req.message)
        await _persist_chat(
            req.session_id or f"api-{datetime.now(UTC).timestamp()}",
            req.message,
            answer,
            trajectory,
        )
        return {"answer": answer, "trajectory": trajectory, "provider_used": provider}

    @app.post("/api/v1/butler/task")
    async def butler_task(body: ButlerTaskIn) -> dict:
        butler = app.state.butler
        if butler is None:
            raise HTTPException(status_code=503, detail="butler unavailable")
        result = await butler.execute_task(body.task, body.confirm_token)
        return {"ok": True, "data": result}

    @app.get("/api/v1/butler/audit")
    async def butler_audit_list(limit: int = 50) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(
                    select(ButlerAudit).order_by(ButlerAudit.ts.desc()).limit(limit)
                )
            ).all()
        items = [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "tool": r.tool,
                "args": r.args,
                "result": r.result,
                "risk": r.risk,
                "confirmed": r.confirmed,
            }
            for r in rows
        ]
        return {"ok": True, "data": {"count": len(items), "items": items}}
