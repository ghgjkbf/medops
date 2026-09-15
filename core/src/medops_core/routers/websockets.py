"""WebSocket endpoints: dashboard status stream + streaming chat."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from medops_core.agents.llm import rule_based_fallback
from medops_core.agents.secretary import SecretaryAgent
from medops_core.bootstrap import build_llm
from medops_core.routers.agents import _persist_chat
from medops_core.ws import ConnectionManager

_LOG = logging.getLogger("medops.ws")


def register(app: FastAPI) -> None:
    @app.websocket("/ws/dashboard")
    async def ws_dashboard(ws: WebSocket) -> None:
        manager: ConnectionManager = app.state.ws_manager
        await manager.connect(ws)
        try:
            # immediate status snapshot on join, then keep the socket warm
            await ws.send_text(
                json.dumps(
                    {
                        "type": "status",
                        "registry": app.state.registry.list_status(),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            while True:
                # client -> server messages are ignored (heartbeat only)
                await ws.receive_text()
        except WebSocketDisconnect:
            await manager.disconnect(ws)

    @app.websocket("/ws/chat/{session_id}")
    async def ws_chat(ws: WebSocket, session_id: str) -> None:
        await ws.accept()
        try:
            data = json.loads(await ws.receive_text())
            message = str(data.get("message", "")).strip()
            if not message:
                await ws.send_text(
                    json.dumps({"type": "error", "detail": "empty message"})
                )
                await ws.close()
                return

            async def _send(payload: dict) -> None:
                await ws.send_text(
                    json.dumps(payload, ensure_ascii=False, default=str)
                )

            trajectory: list[dict[str, Any]] = []
            provider = "rules"
            if app.state.registry.handles:
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
                answer = result.answer
                trajectory = result.trajectory_dicts()
                provider = result.provider_used
            else:
                answer = rule_based_fallback(message)

            for step in trajectory:  # event-style: trace first, answer last
                await _send({"type": "tool_trace", "step": step})
            await _send(
                {"type": "answer", "answer": answer, "provider_used": provider}
            )

            await _persist_chat(session_id, message, answer, trajectory)
            await ws.close()
        except WebSocketDisconnect:
            _LOG.debug("websocket client disconnected", exc_info=True)
