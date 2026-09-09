"""Pydantic request schemas for the P3-1 resource API (responses are plain
dicts wrapped in the {"ok": true, "data": ...} envelope by the routes)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class DeviceIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    device_type: str = Field(min_length=1, max_length=32)
    model: str = ""
    department: str = ""
    status: str = "online"


class WorkOrderIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""


class WorkOrderPatch(BaseModel):
    status: str | None = None
    title: str | None = None
    description: str | None = None


class MaintenancePlanIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    interval_days: int = Field(ge=1)


class MaintenanceRecordIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    work_order_id: int | None = None
    kind: str = "repair"  # repair | pm
    content: str = Field(min_length=1)
    performed_by: str = ""


class McpServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=255)

    @field_validator("url")
    @classmethod
    def _url_scheme(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return v


class KnowledgeIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    device_type: str | None = None


class ButlerTaskIn(BaseModel):
    task: str = Field(min_length=1)
    confirm_token: str | None = None


class KnowledgeSourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=16)  # web | rss | vector_store
    url: str = Field(min_length=1, max_length=512)
    schedule_minutes: int | None = Field(default=None, ge=5)


class RemediationAgreeIn(BaseModel):
    approve: bool = True
    rule: str | None = None


class PluginStateIn(BaseModel):
    enabled: bool


class PluginRunIn(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class PluginImportIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)
    risk: str = Field(default="safe", max_length=16)
    kind: str = Field(min_length=1, max_length=32)
    config: dict[str, Any] = Field(default_factory=dict)
