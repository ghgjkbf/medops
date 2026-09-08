"""Butler agent — the inspector's management role (P5a).

The inspector doubles as the butler: it receives operation tasks (from the
secretary's operation intents or `POST /api/v1/butler/task`) and executes
them against platform management capabilities via direct service-layer
calls (no self-HTTP; FSM and business rules are re-enforced here).

Risk model:
- LOW actions run immediately (reads, work-order transitions/creation,
  endpoint toggles, MCP registration, triggering an inspection, reports).
- HIGH actions (delete / bulk cleanup / clear / remove) need confirmation:
  the first call returns a pending-confirmation token + preview; the task
  re-issued with that token executes. `MEDOPS_BUTLER_AUTO=1` (demo mode)
  executes HIGH actions immediately.

Every executed action is written to the `butler_audit` table.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from medops_common.constants import (
    WORK_ORDER_TRANSITIONS,
    WorkOrderStatus,
    can_transition_work_order,
)
from sqlalchemy import delete, select, update

from medops_core.mcp_client.registry import MCPServerConfig
from medops_core.models import (
    Alert,
    ButlerAudit,
    ChatSession,
    Device,
    DeviceLog,
    DeviceMetric,
    MaintenancePlan,
    MaintenanceRecord,
    McpServer,
    WorkOrder,
)

_TOKEN_TTL_S = 600.0


@dataclass(frozen=True)
class _OpSpec:
    op: str
    risk: str  # low | high
    pattern: str
    handler: str


# Deterministic operation table — first match wins. Handlers are methods
# on ButlerAgent named `_<handler>`; args are extracted per-op below.
_OPERATIONS: list[_OpSpec] = [
    _OpSpec("transition_work_order", "low",
            r"(?:把|将)\s*工单\s*#?\s*(\d+)\s*(?:转|改为?|置为?)\s*(待接单|处理中|待验证|已?关闭)"
            r"|关闭\s*工单\s*#?\s*(\d+)",
            "_op_transition"),
    _OpSpec("create_work_order", "low",
            r"(?:创建|新建|开)\s*(?:一个\s*)?工单", "_op_create_work_order"),
    _OpSpec("delete_work_order", "high",
            r"删除\s*工单\s*#?\s*(\d+)", "_op_delete_work_order"),
    _OpSpec("delete_alert", "high",
            r"删除\s*告警\s*#?\s*(\d+)", "_op_delete_alert"),
    _OpSpec("delete_device", "high",
            r"删除\s*设备\s*([a-z][a-z0-9-]*-sim-\d+|\S+)", "_op_delete_device"),
    _OpSpec("cleanup_data", "high",
            r"清理|清空(?!.*会话)", "_op_cleanup"),
    _OpSpec("clear_chat_history", "high",
            r"清空\s*(?:全部\s*)?(?:会话|聊天|历史)", "_op_clear_chat"),
    _OpSpec("remove_mcp_server", "high",
            r"移除\s*(?:MCP\s*)?服务\s*[「\"]?([A-Za-z0-9_-]+)", "_op_remove_mcp"),
    _OpSpec("delete_endpoint", "high",
            r"删除\s*(?:外部\s*)?端点\s*[「\"]?([A-Za-z0-9_-]+)", "_op_delete_endpoint"),
    _OpSpec("toggle_endpoint", "low",
            r"(启用|停用)\s*端点\s*[「\"]?([A-Za-z0-9_-]+)", "_op_toggle_endpoint"),
    _OpSpec("register_mcp_server", "low",
            r"注册\s*(?:MCP\s*)?服务\s*[「\"]?([A-Za-z0-9_-]+)[」\"]?\s*(https?://\S+)?",
            "_op_register_mcp"),
    _OpSpec("trigger_inspection", "low",
            r"触发巡检|立即巡检|巡检一次|跑一次巡检", "_op_trigger_inspection"),
    _OpSpec("generate_report", "low",
            r"生成\s*(?:平台\s*)?报告|运营报告", "_op_generate_report"),
    _OpSpec("platform_status", "low",
            r"平台状态|服务状态|巡检状态", "_op_platform_status"),
    _OpSpec("sync_knowledge_source", "low",
            r"同步知识源\s*[「\"]?([A-Za-z0-9_-]+)", "_op_sync_knowledge_source"),
    _OpSpec("list_knowledge_sources", "low",
            r"列出知识源|知识源列表", "_op_list_knowledge_sources"),
]

_STATUS_WORDS = {"待接单": "pending", "处理中": "in_progress",
                 "待验证": "awaiting_verification", "已关闭": "closed", "关闭": "closed"}
_RESOURCE_WORDS = {"告警": "alerts", "日志": "logs", "指标": "metrics"}


class ButlerAgent:
    """Management-role agent: task text -> risk-gated platform operations."""

    def __init__(self, db_factory, registry=None, scheduler=None) -> None:  # noqa: ANN001
        self._db_factory = db_factory
        self._registry = registry
        self._scheduler = scheduler
        self._pending: dict[str, tuple[str, dict[str, Any], float]] = {}

    # ----------------------------------------------------------------- plan
    def _classify(self, task: str) -> tuple[str | None, dict[str, Any]]:
        t = task.strip()
        for spec in _OPERATIONS:
            m = re.search(spec.pattern, t, re.IGNORECASE)
            if m is None:
                continue
            return spec.op, self._extract_args(spec.op, t, m)
        return None, {}

    def _extract_args(self, op: str, task: str, m: re.Match) -> dict[str, Any]:  # noqa: C901, PLR0912
        args: dict[str, Any] = {}
        if op == "transition_work_order":
            wo_id = m.group(1) or m.group(3)
            args["wo_id"] = int(wo_id)
            status_word = next((w for w in _STATUS_WORDS if w in task), None)
            args["status"] = _STATUS_WORDS.get(status_word or "", "closed")
        elif op == "delete_work_order":
            args["wo_id"] = int(m.group(1))
        elif op == "delete_alert":
            args["alert_id"] = int(m.group(1))
        elif op == "delete_device":
            args["device_id"] = m.group(1)
        elif op == "cleanup_data":
            args["resource"] = next(
                (r for w, r in _RESOURCE_WORDS.items() if w in task), "alerts")
            days = re.search(r"(\d+)\s*天", task)
            args["days"] = int(days.group(1)) if days else None
            dev = re.search(r"\b([a-z]+-sim-\d+)\b", task, re.IGNORECASE)
            args["device_id"] = dev.group(1) if dev else None
            args["level"] = next(
                (lv for lv in ("critical", "warning", "info") if lv in task.lower()), None)
        elif op == "remove_mcp_server":
            args["name"] = m.group(1)
        elif op == "delete_endpoint":
            args["name"] = m.group(1)
        elif op == "toggle_endpoint":
            args["name"] = m.group(2)
            args["enabled"] = m.group(1) == "启用"
        elif op == "register_mcp_server":
            args["name"] = m.group(1)
            args["url"] = m.group(2)
        elif op == "sync_knowledge_source":
            args["name"] = m.group(1)
        elif op == "create_work_order":
            dev = re.search(r"\b([a-z]+-sim-\d+)\b", task, re.IGNORECASE)
            args["device_id"] = dev.group(1) if dev else ""
            title = re.search(r"[「\"](.+?)[」\"]", task)
            args["title"] = title.group(1) if title else task
        elif op == "generate_report":
            hours = re.search(r"(\d+)\s*(?:小时|hours?)", task)
            args["hours"] = int(hours.group(1)) if hours else 24
        return args

    # ------------------------------------------------------------------ run
    async def execute_task(self, task: str, confirm_token: str | None = None) -> dict[str, Any]:
        op, args = self._classify(task)
        if op is None:
            return {"status": "unsupported", "task": task}
        spec = next(s for s in _OPERATIONS if s.op == op)

        if spec.risk == "high" and not self._auto_mode():
            if confirm_token is None:
                return self._stage_confirmation(op, args)
            entry = self._pending.pop(confirm_token, None)
            if entry is None:
                return {"status": "error", "error": "unknown confirmation token"}
            p_op, p_args, expires = entry
            if time.time() > expires:
                return {"status": "error", "error": "confirmation expired, re-issue the task"}
            op, args = p_op, p_args
            confirmed = True
        else:
            confirmed = spec.risk == "high"  # auto mode: high risk ran unattended

        handler: Callable[..., Awaitable[dict[str, Any]]] = getattr(self, spec.handler)
        try:
            result = await handler(**args)
        except TypeError as exc:
            result = {"ok": False, "error": f"missing arguments: {exc}"}

        await self._audit(op, args, result, spec.risk, confirmed)
        return {"status": "executed", "operation": op, "risk": spec.risk,
                "confirmed": confirmed, "result": result}

    def _auto_mode(self) -> bool:
        return os.environ.get("MEDOPS_BUTLER_AUTO") == "1"

    def _stage_confirmation(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        token = uuid.uuid4().hex[:12]
        self._pending[token] = (op, args, time.time() + _TOKEN_TTL_S)
        return {
            "status": "pending_confirmation",
            "operation": op,
            "token": token,
            "args": args,
            "preview": f"{op} {args}",
            "hint": "回复「确认 <token>」以执行；10 分钟内有效",
        }

    async def _audit(self, op: str, args: dict, result: dict, risk: str, confirmed: bool) -> None:
        trimmed = {k: (str(v)[:500] if isinstance(v, str) else v) for k, v in result.items()}
        async with self._db_factory() as s:
            s.add(ButlerAudit(tool=op, args=args, result=trimmed, risk=risk, confirmed=confirmed))
            await s.commit()

    # -------------------------------------------------------------- handlers
    async def _op_transition(self, wo_id: int, status: str) -> dict[str, Any]:
        if status not in WORK_ORDER_TRANSITIONS:
            return {"ok": False, "error": f"unknown status {status!r}"}
        async with self._db_factory() as s:
            row = (await s.scalars(select(WorkOrder).where(WorkOrder.id == wo_id))).first()
            if row is None:
                return {"ok": False, "error": "work order not found"}
            if not can_transition_work_order(row.status, status):
                return {"ok": False,
                        "error": f"illegal transition {row.status!r} -> {status!r}"}
            row.status = status
            await s.commit()
            return {"ok": True, "work_order_id": wo_id, "status": status}

    async def _op_create_work_order(self, device_id: str, title: str,
                                    description: str = "") -> dict[str, Any]:
        if not device_id or not title:
            return {"ok": False,
                "error": "device_id 与 title 必填（如：创建工单 给 ct-sim-01 换冷却液）"}
        async with self._db_factory() as s:
            s.add(WorkOrder(device_id=device_id, title=title,
                            description=description, status=WorkOrderStatus.PENDING.value))
            await s.commit()
        return {"ok": True, "device_id": device_id, "title": title}

    async def _op_delete_work_order(self, wo_id: int) -> dict[str, Any]:
        async with self._db_factory() as s:
            row = (await s.scalars(select(WorkOrder).where(WorkOrder.id == wo_id))).first()
            if row is None:
                return {"ok": False, "error": "work order not found"}
            await s.execute(update(Alert).where(Alert.work_order_id == wo_id)
                            .values(work_order_id=None))
            await s.execute(update(MaintenanceRecord)
                            .where(MaintenanceRecord.work_order_id == wo_id)
                            .values(work_order_id=None))
            await s.delete(row)
            await s.commit()
        return {"ok": True, "deleted": wo_id}

    async def _op_delete_alert(self, alert_id: int) -> dict[str, Any]:
        async with self._db_factory() as s:
            row = (await s.scalars(select(Alert).where(Alert.id == alert_id))).first()
            if row is None:
                return {"ok": False, "error": "alert not found"}
            await s.delete(row)
            await s.commit()
        return {"ok": True, "deleted": alert_id}

    async def _op_delete_device(self, device_id: str) -> dict[str, Any]:
        async with self._db_factory() as s:
            row = (await s.scalars(select(Device).where(Device.device_id == device_id))).first()
            if row is None:
                return {"ok": False, "error": "device not found"}
            await s.execute(
                delete(MaintenanceRecord).where(MaintenanceRecord.device_id == device_id))
            await s.execute(delete(Alert).where(Alert.device_id == device_id))
            await s.execute(delete(WorkOrder).where(WorkOrder.device_id == device_id))
            await s.execute(delete(MaintenancePlan).where(MaintenancePlan.device_id == device_id))
            await s.execute(delete(DeviceLog).where(DeviceLog.device_id == device_id))
            await s.execute(delete(DeviceMetric).where(DeviceMetric.device_id == device_id))
            await s.delete(row)
            await s.commit()
        return {"ok": True, "deleted": device_id}

    async def _op_cleanup(self, resource: str, days: int | None = None,
                          device_id: str | None = None, level: str | None = None) -> dict[str, Any]:
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        model = {"alerts": Alert, "logs": DeviceLog, "metrics": DeviceMetric}.get(resource)
        if model is None:
            return {"ok": False, "error": f"unknown resource {resource!r}"}
        ts_col = {"alerts": Alert.created_at,
                  "logs": DeviceLog.ts, "metrics": DeviceMetric.ts}[resource]
        dev_col = {"alerts": Alert.device_id, "logs": DeviceLog.device_id,
                   "metrics": DeviceMetric.device_id}[resource]
        stmt = delete(model)
        if days is not None:
            stmt = stmt.where(ts_col < datetime.now(UTC) - timedelta(days=days))
        if device_id:
            stmt = stmt.where(dev_col == device_id)
        if level and resource == "alerts":
            stmt = stmt.where(Alert.level == level)
        async with self._db_factory() as s:
            result = await s.execute(stmt)
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "resource": resource, "deleted": deleted}

    async def _op_clear_chat(self) -> dict[str, Any]:
        async with self._db_factory() as s:
            result = await s.execute(delete(ChatSession))
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "deleted_sessions": deleted}

    async def _op_remove_mcp(self, name: str) -> dict[str, Any]:
        removed = False
        if self._registry is not None:
            removed = await self._registry.remove(name)
        async with self._db_factory() as s:
            row = (await s.scalars(select(McpServer).where(McpServer.name == name))).first()
            db_deleted = False
            if row is not None:
                await s.delete(row)
                await s.commit()
                db_deleted = True
        if not removed and not db_deleted:
            return {"ok": False, "error": "mcp server not found"}
        return {"ok": True, "removed": name}

    async def _op_register_mcp(self, name: str, url: str | None) -> dict[str, Any]:
        if not url:
            return {"ok": False, "error": "缺少端点 URL（如：注册服务 ct http://127.0.0.1:8801/mcp）"}
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "url must start with http:// or https://"}
        if self._registry is None:
            return {"ok": False, "error": "registry unavailable"}
        handle = self._registry.register(MCPServerConfig(name=name, url=url))
        await handle.connect()
        state = handle.state.value
        tools = list(handle.tools)
        async with self._db_factory() as s:
            row = (await s.scalars(select(McpServer).where(McpServer.name == name))).first()
            if row is None:

                row = McpServer(name=name, endpoint=url)
                s.add(row)
            row.transport = "streamable-http"
            row.endpoint = url
            row.health = state
            row.tool_list = [{"name": t} for t in tools]
            await s.commit()
        return {"ok": True, "name": name, "state": state, "tools": len(tools)}

    async def _op_toggle_endpoint(self, name: str, enabled: bool) -> dict[str, Any]:
        from medops_core.api_registry import EndpointRegistry  # noqa: PLC0415

        registry = EndpointRegistry(self._db_factory)
        eps = {e["name"] for e in await registry.list_endpoints(enabled_only=False)}
        if name not in eps:
            return {"ok": False, "error": "endpoint not found"}
        await registry.set_enabled(name, enabled)
        return {"ok": True, "name": name, "enabled": enabled}

    async def _op_delete_endpoint(self, name: str) -> dict[str, Any]:
        from medops_core.api_registry import EndpointRegistry  # noqa: PLC0415

        if not await EndpointRegistry(self._db_factory).delete(name):
            return {"ok": False, "error": "endpoint not found"}
        return {"ok": True, "deleted": name}

    async def _op_trigger_inspection(self) -> dict[str, Any]:
        if self._scheduler is None:
            return {"ok": False, "error": "scheduler unavailable"}
        result = await self._scheduler.run_now()
        return {"ok": True, "checked_servers": result.checked_servers,
                "alerts_created": result.alerts_created,
                "work_orders_created": result.work_orders_created}

    async def _op_generate_report(self, hours: int = 24) -> dict[str, Any]:
        from medops_core.reporting import generate_report  # noqa: PLC0415

        data = await generate_report(self._db_factory, hours)
        return {"ok": True, "window_hours": hours, "markdown": data["markdown"]}

    async def _op_platform_status(self) -> dict[str, Any]:
        async with self._db_factory() as s:
            devices = len((await s.scalars(select(Device))).all())
            orders = len((await s.scalars(select(WorkOrder))).all())
            alerts = len((await s.scalars(select(Alert))).all())
        registry_status = self._registry.list_status() if self._registry else []
        return {"ok": True, "devices": devices, "work_orders": orders,
                "alerts": alerts,
                "mcp_servers": [
                    {"name": h["name"], "state": h["state"]} for h in registry_status]}

    async def _op_sync_knowledge_source(self, name: str) -> dict[str, Any]:
        from medops_core import sources  # noqa: PLC0415

        rows = await sources.list_sources(self._db_factory)
        row = next((r for r in rows if r["name"] == name), None)
        if row is None:
            return {"ok": False, "error": "knowledge source not found"}
        return await sources.sync_source(self._db_factory, row["id"])

    async def _op_list_knowledge_sources(self) -> dict[str, Any]:
        from medops_core import sources  # noqa: PLC0415

        rows = await sources.list_sources(self._db_factory)
        return {"ok": True, "count": len(rows),
                "sources": [{"name": r["name"], "type": r["type"],
                             "status": r["status"]} for r in rows]}
