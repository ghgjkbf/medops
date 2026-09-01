"""Pydantic v2 schemas shared by medops-engine and mcp_server."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from medops_common.constants import DeviceType


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class MetricPoint(BaseModel):
    """A single metric sample from a device (engine 指标流)."""

    model_config = ConfigDict()

    device_id: str
    metric_name: str
    value: float
    ts: datetime = Field(default_factory=_utc_now)
    tags: dict[str, str] = Field(default_factory=dict)


class LogEvent(BaseModel):
    """A log event from a device (engine 日志流)."""

    model_config = ConfigDict()

    device_id: str
    level: LogLevel
    message: str
    ts: datetime = Field(default_factory=_utc_now)
    source: str = ""


class Heartbeat(BaseModel):
    """A device heartbeat (engine 心跳流)."""

    model_config = ConfigDict()

    device_id: str
    seq: int
    ts: datetime = Field(default_factory=_utc_now)


class DeviceStatus(BaseModel):
    """Device status snapshot (mcp_server 设备状态)."""

    model_config = ConfigDict()

    device_id: str
    device_type: DeviceType
    online: bool
    scenario: str | None = None
    last_heartbeat: datetime | None = None
