"""P3-3: WebSocket tests — dashboard connect/status + chat event flow +
ConnectionManager fan-out/disconnect."""

from __future__ import annotations

import json

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from medops_core.app import create_app
from medops_core.mcp_client.sync import _sync_url
from medops_core.models import Base
from medops_core.ws import ConnectionManager
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool


# ------------------------------------------------------ ConnectionManager
async def test_manager_broadcast_and_disconnect() -> None:
    """Broadcast reaches every connected client; disconnect cleans up."""
    manager = ConnectionManager()
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(ws: WebSocket) -> None:
        await manager.connect(ws)
        try:
            while True:
                await ws.receive_text()  # keep warm; server pushes below
        except Exception:  # noqa: BLE001 - disconnect
            await manager.disconnect(ws)

    client = TestClient(app)
    with client.websocket_connect("/ws") as wsc:
        assert manager.count == 1
        await manager.broadcast({"ping": 1})
        assert wsc.receive_json() == {"ping": 1}
    # client gone: broadcast must not raise, dead socket gets dropped
    await manager.broadcast({"ping": 2})
    assert manager.count == 0


# ---------------------------------------------------------- app dashboard
def test_dashboard_ws_status_snapshot(db_engine: AsyncEngine) -> None:
    _prepare(db_engine)
    application = create_app()
    _wire(application, db_engine)
    client = TestClient(application)
    with client.websocket_connect("/ws/dashboard") as wsc:
        first = wsc.receive_json()
        assert first["type"] == "status"
        assert "registry" in first


# --------------------------------------------------------------- chat ws
def test_chat_ws_event_flow(db_engine: AsyncEngine) -> None:
    _prepare(db_engine)
    application = create_app()
    _wire(application, db_engine)
    client = TestClient(application)
    with client.websocket_connect("/ws/chat/s-ws-1") as wsc:
        wsc.send_text(json.dumps({"message": "status of ct-3"}))
        # events end with the answer; trace events may or may not appear
        answer = None
        for _ in range(20):
            msg = wsc.receive_json()
            if msg["type"] == "answer":
                answer = msg
                break
            assert msg["type"] in ("tool_trace", "error")
        assert answer is not None
        assert "answer" in answer and "provider_used" in answer


def test_chat_ws_empty_message_error(db_engine: AsyncEngine) -> None:
    _prepare(db_engine)
    application = create_app()
    _wire(application, db_engine)
    client = TestClient(application)
    with client.websocket_connect("/ws/chat/s-empty") as wsc:
        wsc.send_text(json.dumps({"message": ""}))
        msg = wsc.receive_json()
        assert msg["type"] == "error"


def _prepare(db_engine: AsyncEngine) -> None:
    engine = sync_create_engine(_sync_url(str(db_engine.url)))
    Base.metadata.create_all(engine)
    engine.dispose()


def _wire(application, db_engine: AsyncEngine) -> None:  # noqa: ANN001
    engine = create_async_engine(str(db_engine.url), poolclass=NullPool)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    application.state.db_factory = async_sessionmaker(engine, expire_on_commit=False)
