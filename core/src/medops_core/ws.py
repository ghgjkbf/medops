"""P3-3: WebSocket hub — dashboard status/alert push + chat event stream.

Design (per plan):
- /ws/dashboard: periodic MCP status summary + real-time alert events.
  Alerts flow inspector -> AlertingEngine.notify -> sinks (the P2 sink
  abstraction); WebSocketSink fans out to connected clients. Failure of
  notification must never break the inspection loop.
- /ws/chat/{session_id}: event-style (not token streaming — LLMClient has
  no stream interface; token streaming is a P4 backlog item). Sequence:
  {"type":"tool_trace", ...} events, then a final {"type":"answer", ...}.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    """Fan-out hub for dashboard WebSocket connections."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        return len(self._connections)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._connections:
                self._connections.remove(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        async with self._lock:
            dead: list[WebSocket] = []
            for ws in self._connections:
                try:
                    await ws.send_text(text)
                except Exception:  # noqa: BLE001 - dead peer
                    dead.append(ws)
            for ws in dead:
                self._connections.remove(ws)


class WebSocketSink:
    """NotificationSink adapter: push alert notifications to dashboard clients.

    Follows the P2 NotificationSink protocol (sync callable receiving a
    dict); broadcast is scheduled on the running loop.
    """

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager

    def __call__(self, notification: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (e.g. sync test context): skip push
        loop.create_task(self._manager.broadcast({"type": "alert", **notification}))
