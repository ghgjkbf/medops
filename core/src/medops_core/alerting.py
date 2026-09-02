"""Alerting engine: severity escalation + notification fan-out (P2-7).

Alerts come from the log pipeline and the inspector. This module adds:
- severity escalation when the same device keeps alerting,
- a notification sink interface (WebSocket fan-out lands in P3; the
  console sink is active so demos show the flow).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from medops_common.constants import AlertLevel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from medops_core.db import get_database_url
from medops_core.models import Alert

ESCALATION_THRESHOLD = 3  # alerts for one device within the window -> escalate

NotificationSink = Callable[[dict[str, Any]], None]


def _console_sink(notification: dict[str, Any]) -> None:
    print(f"[NOTIFY] {notification}")


class AlertingEngine:
    """Reads recent alerts, escalates repeat offenders, notifies sinks."""

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        sinks: list[NotificationSink] | None = None,
    ) -> None:
        self._engine = engine or create_async_engine(get_database_url())
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        self.sinks: list[NotificationSink] = sinks if sinks is not None else [_console_sink]

    async def close(self) -> None:
        await self._engine.dispose()

    async def recent_counts(self, window_s: int = 3600) -> Counter:
        since = datetime.now(UTC).timestamp() - window_s
        since_dt = datetime.fromtimestamp(since, tz=UTC)
        async with self._session_factory() as s:
            rows = (
                await s.scalars(select(Alert).where(Alert.created_at >= since_dt))
            ).all()
            return Counter(r.device_id for r in rows)

    async def escalate_if_needed(self, device_id: str, window_s: int = 3600) -> bool:
        """If the device alerted >= threshold times recently, raise ALL its
        recent WARNING alerts to CRITICAL and notify. Returns True if escalated."""
        counts = await self.recent_counts(window_s)
        if counts.get(device_id, 0) < ESCALATION_THRESHOLD:
            return False
        since_dt = datetime.now(UTC).timestamp() - window_s
        since = datetime.fromtimestamp(since_dt, tz=UTC)
        async with self._session_factory() as s:
            rows = (
                await s.scalars(
                    select(Alert)
                    .where(Alert.device_id == device_id)
                    .where(Alert.created_at >= since)
                    .where(Alert.level == AlertLevel.WARNING.value)
                )
            ).all()
            if not rows:
                return False
            for row in rows:
                row.level = AlertLevel.CRITICAL.value
                row.attribution = (row.attribution or "") + (
                    " [升级] 短时间内重复告警，已自动升级为 CRITICAL"
                )
            await s.commit()
            notification = {
                "type": "escalation",
                "device_id": device_id,
                "escalated": len(rows),
                "level": AlertLevel.CRITICAL.value,
            }
            for sink in self.sinks:
                sink(notification)
            return True

    async def notify_all(self) -> int:
        """Push a summary notification for all unresolved (pending) alerts."""
        async with self._session_factory() as s:
            rows = (
                await s.scalars(
                    select(Alert)
                    .where(Alert.level.in_([AlertLevel.WARNING.value, AlertLevel.CRITICAL.value]))
                    .order_by(Alert.created_at.desc())
                    .limit(50)
                )
            ).all()
        summary = {
            "type": "summary",
            "count": len(rows),
            "alerts": [
                {"id": a.id, "device_id": a.device_id, "level": a.level, "message": a.message[:80]}
                for a in rows
            ],
        }
        for sink in self.sinks:
            sink(summary)
        return len(rows)
