"""P3-2: report generation (design §7 report builtin tool).

Aggregates device health / alert stats / work-order stats over a time
window into a Markdown report + JSON detail. LLM polish is optional and
degrades silently to the plain template.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from medops_core.models import Alert, Device, DeviceLog, MaintenanceRecord, WorkOrder


async def generate_report(
    session_factory,  # noqa: ANN001 - async_sessionmaker
    hours: int = 24,
    llm=None,  # noqa: ANN001 - optional LLM for polish
) -> dict:
    """Build {markdown, detail} for the last `hours` window."""
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    async with session_factory() as s:
        devices = (await s.scalars(select(Device))).all()
        total_alerts = (
            await s.scalar(
                select(func.count()).select_from(Alert).where(Alert.created_at >= since)
            )
        ) or 0
        critical_alerts = (
            await s.scalar(
                select(func.count())
                .select_from(Alert)
                .where(Alert.created_at >= since, Alert.level == "critical")
            )
        ) or 0
        maintenance_alerts = (
            await s.scalar(
                select(func.count())
                .select_from(Alert)
                .where(Alert.created_at >= since, Alert.kind == "maintenance_due")
            )
        ) or 0
        open_orders = (
            await s.scalar(
                select(func.count())
                .select_from(WorkOrder)
                .where(WorkOrder.status != "closed")
            )
        ) or 0
        closed_orders = (
            await s.scalar(
                select(func.count())
                .select_from(WorkOrder)
                .where(WorkOrder.status == "closed")
            )
        ) or 0

    status_counts: dict[str, int] = {}
    for d in devices:
        status_counts[d.status] = status_counts.get(d.status, 0) + 1

    markdown = _render_markdown(
        hours, status_counts, total_alerts, critical_alerts,
        maintenance_alerts, open_orders, closed_orders,
    )
    if llm is not None:
        markdown = await _polish(llm, markdown)

    return {
        "markdown": markdown,
        "detail": {
            "window_hours": hours,
            "generated_at": now.isoformat(),
            "device_status_counts": status_counts,
            "alerts": {
                "total": total_alerts,
                "critical": critical_alerts,
                "maintenance_due": maintenance_alerts,
            },
            "work_orders": {"open": open_orders, "closed": closed_orders},
        },
    }


def _render_markdown(
    hours: int,
    status_counts: dict[str, int],
    total_alerts: int,
    critical_alerts: int,
    maintenance_alerts: int,
    open_orders: int,
    closed_orders: int,
) -> str:
    status_line = "、".join(f"{k} {v}" for k, v in status_counts.items()) or "无设备"
    return (
        f"# 巡检报告（最近 {hours} 小时）\n\n"
        f"## 设备健康概览\n"
        f"- 设备总数/状态分布：{status_line}\n\n"
        f"## 告警统计\n"
        f"- 告警总数：{total_alerts}（critical {critical_alerts}、"
        f"维保提醒 {maintenance_alerts}）\n\n"
        f"## 工单统计\n"
        f"- 进行中：{open_orders}；已关闭：{closed_orders}\n"
    )


async def _polish(llm, markdown: str) -> str:  # noqa: ANN001
    """Optional LLM polish; any failure keeps the template text."""
    try:
        from medops_core.agents.llm import Message  # noqa: PLC0415

        result = await llm.chat(
            [Message(role="user", content=f"润色以下巡检报告，保持事实与数字不变：\n\n{markdown}")]
        )
        return result.text or markdown
    except Exception:  # noqa: BLE001 - degrade to template
        return markdown


_DEVICE_TYPE_WORDS: dict[str, str] = {
    "呼吸机": "ventilator",
    "心电": "ecg",
    "ct": "ct",
    "dr": "dr",
}


def _resolve_device_scope(question: str | None) -> tuple[str | None, str | None]:
    """(device_id, device_type) mentioned in a question; both None = all."""
    if not question:
        return None, None
    q = question.lower()
    import re  # noqa: PLC0415

    m = re.search(r"\b([a-z]+-sim-\d+)\b", q)
    if m:
        return m.group(1), None
    for word, dtype in _DEVICE_TYPE_WORDS.items():
        if word in q:
            return None, dtype
    return None, None


async def device_fault_report(
    session_factory,  # noqa: ANN001 - async_sessionmaker
    question: str | None = None,
) -> dict:
    """Per-device fault report (Agent tool): alerts + warn/error logs +
    work orders + maintenance records, scoped by the device mentioned in
    `question` (falls back to all devices). Returns {scope, markdown, ...}.
    """
    device_id, device_type = _resolve_device_scope(question)
    async with session_factory() as s:
        stmt = select(Device).order_by(Device.device_id)
        if device_id:
            stmt = stmt.where(Device.device_id == device_id)
        elif device_type:
            stmt = stmt.where(Device.device_type == device_type)
        devices = (await s.scalars(stmt)).all()
        if not devices:  # unknown device -> report across all
            devices = (await s.scalars(select(Device).order_by(Device.device_id))).all()
        scope = [d.device_id for d in devices]

        alerts = (
            await s.scalars(
                select(Alert).order_by(Alert.created_at.desc()).limit(40)
            )
        ).all()
        logs = (
            await s.scalars(
                select(DeviceLog)
                .where(DeviceLog.level.in_(("ERROR", "WARNING")))
                .order_by(DeviceLog.ts.desc())
                .limit(40)
            )
        ).all()
        orders = (
            await s.scalars(
                select(WorkOrder).order_by(WorkOrder.created_at.desc()).limit(20)
            )
        ).all()
        records = (
            await s.scalars(
                select(MaintenanceRecord)
                .order_by(MaintenanceRecord.performed_at.desc())
                .limit(20)
            )
        ).all()

    lines: list[str] = [
        f"# 设备故障报告（范围：{', '.join(scope) or '无设备'}）", ""
    ]
    n_alerts = n_logs = n_orders = n_records = 0
    for dev in devices:
        lines.append(f"## {dev.device_id}（{dev.device_type}，{dev.status}）")
        dev_alerts = [a for a in alerts if a.device_id == dev.device_id]
        dev_logs = [log for log in logs if log.device_id == dev.device_id]
        dev_orders = [o for o in orders if o.device_id == dev.device_id]
        dev_records = [r for r in records if r.device_id == dev.device_id]
        n_alerts += len(dev_alerts)
        n_logs += len(dev_logs)
        n_orders += len(dev_orders)
        n_records += len(dev_records)
        if dev_alerts:
            lines.append("### 近期告警")
            for a in dev_alerts[:8]:
                lines.append(
                    f"- [{a.level}] {a.created_at:%m-%d %H:%M} {a.message}"
                    + (f"（工单 #{a.work_order_id}）" if a.work_order_id else "")
                )
        if dev_logs:
            lines.append("### 异常日志")
            for log in dev_logs[:8]:
                lines.append(f"- [{log.level}] {log.ts:%m-%d %H:%M} {log.message[:120]}")
        if dev_orders:
            lines.append("### 工单")
            for o in dev_orders[:5]:
                lines.append(f"- #{o.id} [{o.status}] {o.title}")
        if dev_records:
            lines.append("### 维保记录")
            for r in dev_records[:5]:
                lines.append(f"- {r.performed_at:%m-%d} {r.content[:100]}")
        if not (dev_alerts or dev_logs or dev_orders or dev_records):
            lines.append("- 近期无故障记录")
        lines.append("")

    return {
        "scope": scope,
        "markdown": "\n".join(lines),
        "alert_count": n_alerts,
        "log_count": n_logs,
        "work_order_count": n_orders,
        "record_count": n_records,
    }
