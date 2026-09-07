"""Shared enums for medops: device types, alert levels, work order states, action risk."""

from enum import StrEnum


class DeviceType(StrEnum):
    """Supported medical device types."""

    CT = "ct"
    DR = "dr"
    VENTILATOR = "ventilator"
    ECG = "ecg"


class AlertLevel(StrEnum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class WorkOrderStatus(StrEnum):
    """Work order state machine (工单状态机):

    待接单(PENDING) → 处理中(IN_PROGRESS) → 待验证(AWAITING_VERIFICATION) → 关闭(CLOSED)
    """

    PENDING = "pending"  # 待接单
    IN_PROGRESS = "in_progress"  # 处理中
    AWAITING_VERIFICATION = "awaiting_verification"  # 待验证
    CLOSED = "closed"  # 关闭


class ActionRisk(StrEnum):
    """Action risk tiers (design §5 动作分级)."""

    READ_ONLY = "read_only"  # 只读查询
    LOW_RISK_WRITE = "low_risk_write"  # 低风险写
    HIGH_RISK_WRITE = "high_risk_write"  # 高风险写


# Legal transitions of the work-order state machine (design §6).
# Single source of truth: REST API (core) and the maintenance-db MCP server
# both validate against this table.
WORK_ORDER_TRANSITIONS: dict[str, set[str]] = {
    WorkOrderStatus.PENDING.value: {WorkOrderStatus.IN_PROGRESS.value},
    WorkOrderStatus.IN_PROGRESS.value: {WorkOrderStatus.AWAITING_VERIFICATION.value},
    WorkOrderStatus.AWAITING_VERIFICATION.value: {WorkOrderStatus.CLOSED.value},
    WorkOrderStatus.CLOSED.value: set(),
}


def can_transition_work_order(current: str, new: str) -> bool:
    """True only for a legal state-machine transition between known statuses."""
    return new in WORK_ORDER_TRANSITIONS.get(current, set())
