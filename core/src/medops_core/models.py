"""SQLAlchemy 2.x ORM models for the 11 core tables (design doc §6).

Enumeration values reuse medops_common.constants. Timezone-aware UTC
timestamps throughout; device_metric is designed per TimescaleDB
hypertable conventions (composite PK (device_id, ts)) — compatibility
stated in the paper, not implemented (design D6).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from medops_common.constants import AlertLevel, DeviceType, WorkOrderStatus
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all medops core tables."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


# JSONB on PostgreSQL, plain JSON elsewhere (SQLite fallback).
JSONVariant = JSONB().with_variant(JSON(), "sqlite")


class McpServer(Base):
    """MCP 服务注册表：名称/传输协议/端点/健康度/工具列表/最后心跳。"""

    __tablename__ = "mcp_server"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False, default="streamable-http")
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    health: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    tool_list: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONVariant, nullable=False, default=list
    )
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class Device(Base):
    """设备台账：类型/型号/科室/状态/注册的 MCP Server。"""

    __tablename__ = "device"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    device_type: Mapped[str] = mapped_column(
        Enum(*[t.value for t in DeviceType], name="device_type", length=32),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    department: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="online")
    mcp_server_id: Mapped[int | None] = mapped_column(
        ForeignKey("mcp_server.id", ondelete="SET NULL")
    )

    mcp_server: Mapped[McpServer | None] = relationship(lazy="selectin")


class DeviceMetric(Base):
    """时序指标；复合主键 (device_id, ts) 符合 hypertable 规范（D6，兼容不实做）。"""

    __tablename__ = "device_metric"
    __table_args__ = (Index("ix_device_metric_ts", "ts"),)

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    metric_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    tags: Mapped[dict[str, str]] = mapped_column(JSONVariant, nullable=False, default=dict)


class DeviceLog(Base):
    """设备日志原文与结构化字段。"""

    __tablename__ = "device_log"
    __table_args__ = (Index("ix_device_log_ts", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class Alert(Base):
    """预警：级别/归因（LLM 或规则降级摘要）/关联工单。"""

    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(
        Enum(*[a.value for a in AlertLevel], name="alert_level", length=16),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    attribution: Mapped[str | None] = mapped_column(Text)  # LLM 归因或规则降级摘要
    work_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_order.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # selectin: async sessions must not trigger sync lazy loads on access.
    work_order: Mapped[WorkOrder | None] = relationship(
        back_populates="alerts", lazy="selectin"
    )


class WorkOrder(Base):
    """工单状态机：待接单→处理中→待验证→关闭。"""

    __tablename__ = "work_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        Enum(*[s.value for s in WorkOrderStatus], name="work_order_status", length=32),
        nullable=False,
        default=WorkOrderStatus.PENDING.value,
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    alerts: Mapped[list[Alert]] = relationship(
        back_populates="work_order", lazy="selectin"
    )
    repair_records: Mapped[list[MaintenanceRecord]] = relationship(
        back_populates="work_order", lazy="selectin"
    )


class MaintenancePlan(Base):
    """周期维保计划。"""

    __tablename__ = "maintenance_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    last_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MaintenanceRecord(Base):
    """维保/维修记录。"""

    __tablename__ = "maintenance_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    work_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_order.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="repair")  # repair|pm
    content: Mapped[str] = mapped_column(Text, nullable=False)
    performed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    work_order: Mapped[WorkOrder | None] = relationship(
        back_populates="repair_records", lazy="selectin"
    )


class ChatSession(Base):
    """秘书 Agent 会话。"""

    __tablename__ = "chat_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ChatMessage(Base):
    """会话消息（含工具调用轨迹 JSON）。"""

    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_session.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|assistant|tool
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_trace: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONVariant)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class KnowledgeDoc(Base):
    """知识库文档（embedding 在知识库检索落地时补列）。"""

    __tablename__ = "knowledge_doc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ApiEndpoint(Base):
    """外部 API 接入配置（P2.5）。

    统一登记外部系统的 URL 与凭据，双向使用：
    - 出站：LLM 降级链把 OpenAI-compatible 端点并入 provider 列表；
      Agent 经 `call_external_api` builtin 工具调用任意已登记端点。
    - 入站：外部系统持 X-API-Key 访问 medops 全部 /api/v1 接口。

    auth_type: bearer | header | none（header 用 api_header 指定自定义头名）
    """

    __tablename__ = "api_endpoint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False, default="bearer")
    api_header: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))  # OpenAI-compatible 端点用
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="generic")  # llm|generic
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
