"""Inspector agent: scheduled inspection -> attribution -> alert/work order.

Implements the inspection chain (design §5.1): for each registered MCP
server, call its detection tools, evaluate thresholds, attribute anomalies
via the LLM (rule fallback when down), store Alerts, and auto-create a
WorkOrder for critical findings. Action grading (§5.1): read-only tools run
automatically; HIGH_RISK_WRITE tools are never invoked here — critical
findings only produce a work order for human confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from medops_common.constants import AlertLevel, WorkOrderStatus
from medops_common.thresholds import THRESHOLD_RULES
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from medops_core.agents.base import BaseAgent
from medops_core.log_pipeline.rules import RuleHit
from medops_core.models import Alert, WorkOrder

# Tools that never run inside an automated inspection (HIGH_RISK_WRITE).
_FORBIDDEN_TOOLS = {"set_fault_scenario", "create_work_order", "update_work_order"}

# Detection tools worth calling per server name prefix (device servers).
_DETECTION_TOOLS = {
    "ct": ["get_tube_stats", "check_dicom_dir", "check_pacs_connectivity",
           "get_device_info"],
    "dr": ["get_detector_temp", "check_generator_status", "get_device_info"],
    "ventilator": ["get_realtime_params", "run_self_test", "get_device_info"],
    "ecg": ["get_waveform_quality"],
}


def evaluate_device_system(device_id: str, payload: dict) -> list[RuleHit]:
    """P6a: the device's OWN system — firmware / config / onboard agent."""
    system = (payload or {}).get("device_system")
    if not isinstance(system, dict) or not system:
        return []
    hits: list[RuleHit] = []
    now = datetime.now(UTC).isoformat()
    if str(system.get("firmware_version", "")) != str(system.get("target_firmware_version", "")):
        hits.append(RuleHit(
            device_id=device_id, level="warning",
            message=(f"firmware {system.get('firmware_version')} < required "
                     f"{system.get('target_firmware_version')}"),
            rule="device_system:firmware-stale", ts=now,
        ))
    if str(system.get("config_hash", "")) != str(system.get("expected_config_hash", "")):
        hits.append(RuleHit(
            device_id=device_id, level="warning",
            message="config hash drift detected",
            rule="device_system:config-drift", ts=now,
        ))
    if str(system.get("agent_health", "")) != "healthy":
        hits.append(RuleHit(
            device_id=device_id, level="warning",
            message=f"device agent health={system.get('agent_health')}",
            rule="device_system:agent-stuck", ts=now,
        ))
    return hits


@dataclass
class InspectionResult:
    checked_servers: list[str] = field(default_factory=list)
    tool_results: dict[str, Any] = field(default_factory=dict)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    alerts_created: int = 0
    work_orders_created: list[int] = field(default_factory=list)
    provider_used: str = ""


def _extract_metrics(tool: str, payload: dict) -> dict[str, float]:
    """Pull the numeric metrics out of a tool payload (shape per server)."""
    for key in ("metrics", "params"):
        if isinstance(payload.get(key), dict):
            return {k: float(v) for k, v in payload[key].items() if isinstance(v, (int, float))}
    if tool == "get_detector_temp" and payload.get("detector_temp") is not None:
        return {"detector_temp": float(payload["detector_temp"])}
    if tool == "get_waveform_quality" and payload.get("waveform_snr") is not None:
        return {"waveform_snr": float(payload["waveform_snr"])}
    return {}


def evaluate_metrics(device_id: str, metrics: dict[str, float]) -> list[RuleHit]:
    """Threshold evaluation against the shared table (ms-level, no LLM)."""
    hits: list[RuleHit] = []
    now = datetime.now(UTC).isoformat()
    from mcp_fw.snapshot import evaluate_thresholds

    statuses = evaluate_thresholds(metrics, THRESHOLD_RULES)
    for metric, status in statuses.items():
        if status in ("warning", "error"):
            value = metrics[metric]
            rule = THRESHOLD_RULES[metric]
            hits.append(
                RuleHit(
                    device_id=device_id,
                    level=status,
                    message=f"{metric}={value} exceeded {status} threshold {rule}",
                    rule=f"threshold:{metric}:{status}",
                    ts=now,
                )
            )
    return hits


class InspectorAgent(BaseAgent):
    name = "inspector"

    def __init__(
        self,
        llm,
        registry,
        session_factory,
        session: Session | None = None,
        notifier=None,  # noqa: ANN001 - Optional[[list[Alert]], None] callback (P3-3)
        butler=None,  # noqa: ANN001 - ButlerAgent (P5a: inspector doubles as butler)
        remediation=None,  # noqa: ANN001 - RemediationService (P6a)
    ) -> None:
        super().__init__(llm, tools={})
        self._registry = registry
        self._session_factory = session_factory
        self._session = session
        self._notifier = notifier
        self._butler = butler
        self._remediation = remediation

    async def execute_task(self, task: str, confirm_token: str | None = None) -> dict:
        """Butler role (P5a): execute a management task (delegated)."""
        if self._butler is None:
            raise RuntimeError("butler not enabled")
        return await self._butler.execute_task(task, confirm_token)

    async def inspect_with_spec(self, spec: dict) -> InspectionResult:
        """P6b: targeted inspection driven by a secretary requirement spec."""
        devices = spec.get("devices") or []
        if not devices or "all" in devices:
            prefixes = None
        else:
            prefixes = {str(d).split("-")[0] for d in devices}
        return await self.run_inspection(prefixes=prefixes)

    async def plan(self, user_input: str) -> str:  # pragma: no cover - not used
        return ""

    # ------------------------------------------------------------------ core
    async def run_inspection(self, prefixes: set[str] | None = None) -> InspectionResult:
        """Scheduled inspection; ``prefixes`` limits the device types (P6b)."""

        result = InspectionResult()
        mcp_unavailable: list[str] = []
        for handle in self._registry.handles.values():
            if handle.state.value == "unavailable":
                mcp_unavailable.append(handle.config.name)
                continue
            device_type = handle.config.name.split("-")[0]
            if prefixes is not None and device_type not in prefixes:
                continue
            tools = _DETECTION_TOOLS.get(device_type, [])
            if not tools:
                continue
            result.checked_servers.append(handle.config.name)

            for tool in tools:
                if tool in _FORBIDDEN_TOOLS:
                    continue
                try:
                    payload = await handle.call_tool(tool)
                except Exception as exc:  # noqa: BLE001 - degraded detection
                    result.tool_results[f"{handle.config.name}.{tool}"] = {
                        "error": str(exc)
                    }
                    continue
                result.tool_results[f"{handle.config.name}.{tool}"] = payload
                metrics = _extract_metrics(tool, payload if isinstance(payload, dict) else {})
                if metrics:
                    device_id = payload.get("device_id", f"{device_type}-sim-01")
                    result.anomalies.extend(
                        a.__dict__ if False else a
                        for a in evaluate_metrics(device_id, metrics)
                    )
                if tool == "get_device_info":
                    device_id = payload.get("device_id", f"{device_type}-sim-01")
                    result.anomalies.extend(evaluate_device_system(device_id, payload))

        # de-duplicate anomalies by (device_id, metric)
        seen: set[tuple[str, str]] = set()
        unique_anomalies: list[RuleHit] = []
        for a in result.anomalies:
            key = (a.device_id, a.rule)
            if key not in seen:
                seen.add(key)
                unique_anomalies.append(a)
        result.anomalies = unique_anomalies

        if result.anomalies:
            alerts = await self._store_alerts(result.anomalies)
            try:  # KB suggestion attached to attribution (best effort)
                from medops_core import knowledge  # noqa: PLC0415

                await knowledge.enrich_alerts(self._session_factory, alerts)
            except Exception:  # noqa: BLE001 - KB is optional
                pass
            result.alerts_created = len(alerts)
            if self._remediation is not None:
                try:
                    await self._attach_remediation(alerts, result.anomalies, mcp_unavailable)
                except Exception:  # noqa: BLE001 - never break the inspection
                    pass
            critical = [a for a in alerts if a.level == AlertLevel.CRITICAL.value]
            result.work_orders_created = await self._create_work_orders(critical)
            if self._notifier is not None and alerts:
                try:
                    self._notifier(alerts)
                except Exception:  # noqa: BLE001 - push must never break inspection
                    pass
        result.provider_used = getattr(self.llm, "provider_name", "llm")
        return result

    async def _attach_remediation(
        self, alerts: list[Alert], hits: list[RuleHit], signals: list[str]
    ) -> None:
        """P6a: classify each anomaly, run auto-heal when allowed, then attach
        the outcomes (per device) to the alert rows."""

        outcomes: dict[str, list[dict]] = {}
        for hit in hits:
            try:
                outcome = await self._remediation.heal(
                    hit, signals={"mcp_unavailable": signals}
                )
            except Exception as exc:  # noqa: BLE001 - triage must never break inspection
                outcome = {"kind": "unknown", "error": f"{type(exc).__name__}: {exc}"}
            outcomes.setdefault(hit.device_id, []).append(outcome)
        session = self._session or self._session_factory()
        try:
            for alert in alerts:
                meta = dict(alert.meta or {})
                meta["remediation"] = outcomes.get(alert.device_id, [])
                await session.execute(
                    update(Alert).where(Alert.id == alert.id).values(meta=meta)
                )
                alert.meta = meta
            await session.commit()
        finally:
            if self._session is None:
                await session.close()

    async def _store_alerts(self, hits: list[RuleHit]) -> list[Alert]:
        from medops_core.log_pipeline.analyzer import attribute_and_store  # noqa: PLC0415

        session = self._session or self._session_factory()
        try:
            return await attribute_and_store(hits, session, self.llm)
        finally:
            if self._session is None:
                await session.close()

    async def _create_work_orders(self, critical_alerts: list[Alert]) -> list[int]:
        if not critical_alerts:
            return []
        ids: list[int] = []
        session = self._session or self._session_factory()
        try:
            for alert in critical_alerts:
                # dedupe on device+attribution signature: the same recurring
                # fault must not spawn duplicate orders across runs
                signature = (alert.message or "")[:60]
                dedupe = f"inspect-{alert.device_id}-{hash(signature) & 0xFFFF:04x}"
                existing = (
                    await session.scalars(
                        select(WorkOrder).where(WorkOrder.dedupe_key == dedupe)
                    )
                ).first()
                if existing is not None:
                    alert.work_order_id = existing.id
                    ids.append(existing.id)
                    continue
                order = WorkOrder(
                    device_id=alert.device_id,
                    title=f"Critical alert: {alert.message[:80]}",
                    description=(f"自动巡检发现严重异常。归因：{alert.attribution}"),
                    status=WorkOrderStatus.PENDING.value,
                    dedupe_key=dedupe,
                )
                session.add(order)
                await session.flush()
                ids.append(order.id)
                # alert may be detached (created on another session): UPDATE by id
                await session.execute(
                    update(Alert).where(Alert.id == alert.id).values(work_order_id=order.id)
                )
                alert.work_order_id = order.id
            await session.commit()
        finally:
            if self._session is None:
                await session.close()
        return ids
