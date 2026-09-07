"""P3-4: periodic maintenance reminders — scan plans, alert when due.

Due date is derived (interval_days + last_done_at/created_at), no extra
column. Idempotency: on alert we roll last_done_at forward, so a plan
alerts at most once per interval.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from medops_core.models import Alert, MaintenancePlan

REMINDER_KIND = "maintenance_due"


def plan_is_due(plan: MaintenancePlan, now: datetime) -> bool:
    """True when now >= last_done_at (or created fallback) + interval."""
    base = plan.last_done_at or getattr(plan, "created_at", None) or now
    return now >= base + timedelta(days=plan.interval_days)


async def scan_and_remind(
    session_factory,  # noqa: ANN001 - async_sessionmaker
    now: datetime | None = None,
) -> list[Alert]:
    """Alert every due plan; roll last_done_at forward (idempotent per interval).

    Returns the alerts created this scan (possibly empty).
    """
    now = now or datetime.now(UTC)
    created: list[Alert] = []
    async with session_factory() as s:
        plans = (
            await s.scalars(
                select(MaintenancePlan).where(MaintenancePlan.active.is_(True))
            )
        ).all()
        for plan in plans:
            if not plan_is_due(plan, now):
                continue
            alert = Alert(
                device_id=plan.device_id,
                level="warning",
                kind=REMINDER_KIND,
                message=f"maintenance due: {plan.name} (every {plan.interval_days}d)",
                attribution="rule: reminder engine",
            )
            s.add(alert)
            plan.last_done_at = now  # roll the cycle forward
            created.append(alert)
        if created:
            await s.commit()
    return created
