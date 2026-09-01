"""Maintenance-db MCP server: device ledger / work-order / alert tools.

Talks to the medops PostgreSQL schema defined in medops_core.models
(same database as core). MCP tool callbacks are sync functions, so this
server uses a *sync* SQLAlchemy engine (URL scheme swapped to psycopg2)
while core keeps its async engine — same database, two drivers.

Read tools are READ_ONLY; mutating tools carry an ActionRisk annotation
(LOW_RISK_WRITE / HIGH_RISK_WRITE) so the P2 inspector agent can enforce
the action-grading policy (design §5.1).

NOTE: all device data is SIMULATED (毕业设计 / open-source demo —
no real medical devices attached).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mcp_fw import MedopsMCPServer
from medops_common.constants import ActionRisk, WorkOrderStatus
from medops_core.db import get_database_url
from medops_core.models import (
    Alert,
    Device,
    MaintenancePlan,
    MaintenanceRecord,
    WorkOrder,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

SERVER_NAME = "medops-maintenance-db"

# Legal transitions of the work-order state machine (design §6).
_WORK_ORDER_TRANSITIONS: dict[str, set[str]] = {
    WorkOrderStatus.PENDING.value: {WorkOrderStatus.IN_PROGRESS.value},
    WorkOrderStatus.IN_PROGRESS.value: {WorkOrderStatus.AWAITING_VERIFICATION.value},
    WorkOrderStatus.AWAITING_VERIFICATION.value: {WorkOrderStatus.CLOSED.value},
    WorkOrderStatus.CLOSED.value: set(),
}

# Action risk per mutating tool (design §5.1 action grading).
TOOL_RISKS: dict[str, str] = {
    "create_work_order": ActionRisk.LOW_RISK_WRITE.value,
    "update_work_order": ActionRisk.HIGH_RISK_WRITE.value,
    "add_repair_record": ActionRisk.LOW_RISK_WRITE.value,
}


def _sync_url(url: str) -> str:
    """Swap the async driver for a sync one (asyncpg -> psycopg2)."""
    return url.replace("+asyncpg", "+psycopg2").replace("+aiosqlite", "")


class MaintenanceDbServer(MedopsMCPServer):
    """MCP server exposing maintenance-ledger tools backed by PostgreSQL."""

    def __init__(self, database_url: str | None = None) -> None:
        super().__init__(SERVER_NAME)
        url = database_url or get_database_url()
        engine = create_engine(_sync_url(url), pool_pre_ping=True)
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.register_tool(self.query_devices)
        self.register_tool(self.get_maintenance_due)
        self.register_tool(self.create_work_order)
        self.register_tool(self.update_work_order)
        self.register_tool(self.add_repair_record)
        self.register_tool(self.query_alerts)

    # ------------------------------------------------------------------ helpers
    def _risk(self, tool: str) -> dict:
        return {"action_risk": TOOL_RISKS.get(tool, ActionRisk.READ_ONLY.value), "simulated": True}

    # -------------------------------------------------------------------- tools
    def query_devices(
        self, device_type: str | None = None, department: str | None = None
    ) -> dict:
        """List ledger devices, optionally filtered by type / department."""
        stmt = select(Device).order_by(Device.device_id)
        if device_type:
            stmt = stmt.where(Device.device_type == device_type)
        if department:
            stmt = stmt.where(Device.department.contains(department))
        with self._session_factory() as s:
            rows = s.scalars(stmt).all()
            return {
                "count": len(rows),
                "devices": [
                    {
                        "device_id": d.device_id,
                        "device_type": d.device_type,
                        "model": d.model,
                        "department": d.department,
                        "status": d.status,
                    }
                    for d in rows
                ],
                **self._risk("query_devices"),
            }

    def get_maintenance_due(self, days_ahead: int = 30) -> dict:
        """Maintenance plans due within ``days_ahead`` days (or overdue)."""
        with self._session_factory() as s:
            plans = s.scalars(
                select(MaintenancePlan).where(MaintenancePlan.active.is_(True))
            ).all()
            due = []
            for p in plans:
                if p.last_done_at is None:
                    due_in = -1  # never serviced -> overdue
                else:
                    next_due = p.last_done_at + timedelta(days=p.interval_days)
                    if next_due.tzinfo is None:
                        next_due = next_due.replace(tzinfo=UTC)
                    due_in = (next_due - datetime.now(UTC)).days
                if due_in <= days_ahead:
                    due.append(
                        {
                            "device_id": p.device_id,
                            "plan": p.name,
                            "interval_days": p.interval_days,
                            "last_done_at": p.last_done_at.isoformat() if p.last_done_at else None,
                            "due_in_days": due_in,
                            "overdue": due_in < 0,
                        }
                    )
            return {
                "days_ahead": days_ahead,
                "count": len(due),
                "plans": sorted(due, key=lambda x: x["due_in_days"]),
                **self._risk("get_maintenance_due"),
            }

    def create_work_order(
        self,
        device_id: str,
        title: str,
        description: str = "",
        dedupe_key: str | None = None,
    ) -> dict:
        """Create a work order (LOW_RISK_WRITE). Idempotent via dedupe_key."""
        with self._session_factory() as s:
            if dedupe_key:
                existing = s.scalars(
                    select(WorkOrder).where(WorkOrder.dedupe_key == dedupe_key)
                ).first()
                if existing is not None:
                    return {
                        "created": False,
                        "work_order_id": existing.id,
                        "status": existing.status,
                        "dedupe_key": dedupe_key,
                        "note": "dedupe_key matched an existing order",
                        **self._risk("create_work_order"),
                    }
            order = WorkOrder(
                device_id=device_id,
                title=title,
                description=description,
                dedupe_key=dedupe_key,
            )
            s.add(order)
            s.commit()
            return {
                "created": True,
                "work_order_id": order.id,
                "status": order.status,
                "device_id": device_id,
                **self._risk("create_work_order"),
            }

    def update_work_order(self, work_order_id: int, new_status: str) -> dict:
        """Transition a work order (HIGH_RISK_WRITE). Illegal transitions rejected."""
        if new_status not in _WORK_ORDER_TRANSITIONS:
            return {
                "updated": False,
                "error": f"unknown status {new_status!r}",
                "legal": sorted(_WORK_ORDER_TRANSITIONS),
                **self._risk("update_work_order"),
            }
        with self._session_factory() as s:
            order = s.get(WorkOrder, work_order_id)
            if order is None:
                return {
                    "updated": False,
                    "error": f"work order {work_order_id} not found",
                    **self._risk("update_work_order"),
                }
            legal = _WORK_ORDER_TRANSITIONS[order.status]
            if new_status not in legal:
                return {
                    "updated": False,
                    "error": (
                        f"illegal transition {order.status!r} -> {new_status!r}"
                    ),
                    "legal_from_current": sorted(legal),
                    **self._risk("update_work_order"),
                }
            order.status = new_status
            s.commit()
            return {
                "updated": True,
                "work_order_id": order.id,
                "status": order.status,
                **self._risk("update_work_order"),
            }

    def add_repair_record(
        self,
        device_id: str,
        content: str,
        work_order_id: int | None = None,
        performed_by: str = "medops-agent",
    ) -> dict:
        """Append a repair/maintenance record (LOW_RISK_WRITE)."""
        with self._session_factory() as s:
            if work_order_id is not None and s.get(WorkOrder, work_order_id) is None:
                return {
                    "added": False,
                    "error": f"work order {work_order_id} not found",
                    **self._risk("add_repair_record"),
                }
            record = MaintenanceRecord(
                device_id=device_id,
                content=content,
                work_order_id=work_order_id,
                performed_by=performed_by,
            )
            s.add(record)
            s.commit()
            return {
                "added": True,
                "record_id": record.id,
                "performed_at": record.performed_at.isoformat(),
                **self._risk("add_repair_record"),
            }

    def query_alerts(
        self, level: str | None = None, since_iso: str | None = None
    ) -> dict:
        """Query alerts, optionally by level and/or created-after timestamp."""
        stmt = select(Alert).order_by(Alert.created_at.desc()).limit(100)
        if level:
            stmt = stmt.where(Alert.level == level)
        if since_iso:
            since = datetime.fromisoformat(since_iso)
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
            stmt = stmt.where(Alert.created_at >= since)
        with self._session_factory() as s:
            rows = s.scalars(stmt).all()
            return {
                "count": len(rows),
                "alerts": [
                    {
                        "id": a.id,
                        "device_id": a.device_id,
                        "level": a.level,
                        "message": a.message,
                        "attribution": a.attribution,
                        "work_order_id": a.work_order_id,
                        "created_at": a.created_at.isoformat(),
                    }
                    for a in rows
                ],
                **self._risk("query_alerts"),
            }


def build_server(database_url: str | None = None) -> MaintenanceDbServer:
    """Factory used by tests and the CLI entry point."""
    return MaintenanceDbServer(database_url=database_url)
