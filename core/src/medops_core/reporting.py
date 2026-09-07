"""P3-2: report generation (design §7 report builtin tool).

Aggregates device health / alert stats / work-order stats over a time
window into a Markdown report + JSON detail. LLM polish is optional and
degrades silently to the plain template.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from medops_core.models import Alert, Device, WorkOrder


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
