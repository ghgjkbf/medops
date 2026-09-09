"""Fault triage & remediation (P6a).

Three channels once a problem is found:

- ``platform_sw``: platform/infra software problems (MCP server down, endpoint
  down, data volume pressure) — remediated through the butler ops;
  low-risk auto, high-risk needs consent.
- ``device_sw``: the device's own system (firmware / config / onboard agent) —
  ALWAYS asks the user before touching the device.
- ``hardware``: physical-device problems — a remediation package (cause,
  steps, risk, verification) plus (optionally) guided mode; nothing is
  executed automatically.

Classification is rule-first deterministic; when a real LLM is attached it
checks the verdict (``llm.check_classification``) but never runs without rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from medops_core.log_pipeline.rules import RuleHit

KIND_PLATFORM_SW = "platform_sw"
KIND_DEVICE_SW = "device_sw"
KIND_HARDWARE = "hardware"

DEVICE_SW_RULES = (
    "device_system:firmware-stale",
    "device_system:config-drift",
    "device_system:agent-stuck",
)

# rule -> (device MCP tool, args) used to repair the device's own system.
DEVICE_REPAIR_TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    "device_system:firmware-stale": ("upgrade_firmware", {}),
    "device_system:config-drift": ("apply_config", {}),
    "device_system:agent-stuck": ("restart_device_agent", {}),
}

_RISKS = {
    "register_mcp_server": "low",
    "toggle_endpoint": "low",
    "cleanup_data": "high",
    "create_work_order": "low",
}


@dataclass
class Classification:
    kind: str
    rule: str
    device_id: str
    message: str
    signals: dict[str, Any] = field(default_factory=dict)
    llm_verified: bool = False


def _hit_attr(hit: RuleHit | dict, key: str, default: Any = "") -> Any:
    return getattr(hit, key, hit.get(key, default) if isinstance(hit, dict) else default)


def classify(
    hit: RuleHit | dict,
    signals: dict[str, Any] | None = None,
    llm: Any | None = None,
) -> Classification:
    """Rule-first triage; optional LLM check only validates/overrides."""
    signals = signals or {}
    rule = str(_hit_attr(hit, "rule", ""))
    message = str(_hit_attr(hit, "message", ""))
    device_id = str(_hit_attr(hit, "device_id", ""))
    if rule in DEVICE_SW_RULES or rule.startswith("device_system:"):
        kind = KIND_DEVICE_SW
    elif rule.startswith("mcp:") or rule.startswith("platform:"):
        kind = KIND_PLATFORM_SW
    elif "unavailable" in message and "MCP" in message:
        kind = KIND_PLATFORM_SW
    else:
        kind = KIND_HARDWARE  # device metric thresholds are hardware by default
    plan = Classification(kind, rule, device_id, message, signals)
    if llm is not None:
        try:
            verdict = llm.check_classification(
                {"rule": rule, "message": message, "device_id": device_id, "kind": kind}
            )
            if verdict is not None and verdict.get("kind") in (
                KIND_PLATFORM_SW,
                KIND_DEVICE_SW,
                KIND_HARDWARE,
            ):
                plan.kind = verdict["kind"]
            plan.llm_verified = True
        except Exception:  # noqa: BLE001 - LLM is advisory only
            plan.llm_verified = False
    return plan


def platform_actions(plan: Classification) -> list[dict[str, Any]]:
    """Map a platform software finding to the butler task that fixes it."""
    named = plan.signals.get("mcp_unavailable") or []
    if named:
        name = str(named[0])
        return [
            {
                "op": "register_mcp_server",
                "task": f"注册 MCP 服务 {name}",
                "risk": "low",
                "params": {"name": name, "url": ""},
            }
        ]
    message = plan.message or ""
    m = re.search(r"endpoint\s+([A-Za-z0-9_-]+)", message, re.IGNORECASE)
    if m:
        return [
            {
                "op": "toggle_endpoint",
                "task": f"启用端点 {m.group(1)}",
                "risk": "low",
                "params": {"name": m.group(1), "enabled": True},
            }
        ]
    if "清理" in message or "存储" in message or "volume" in message.lower():
        return [{"op": "cleanup_data", "task": "清理数据", "risk": "high", "params": {}}]
    return [{"op": "create_work_order", "task": "创建工单", "risk": "low", "params": {}}]


async def read_device_system(handle: Any) -> dict[str, Any]:
    """Read the device-system block via the device's MCP tool (or raw payload)."""
    payload = await handle.call_tool("get_device_info")
    info = payload.get("device_system") if isinstance(payload, dict) else {}
    return info if isinstance(info, dict) else {}


class RemediationService:
    """Triage + repair executor. Never touches a device without consent."""

    def __init__(self, registry: Any, butler: Any, session_factory: Any = None) -> None:
        self._registry = registry
        self._butler = butler
        self._session_factory = session_factory

    def _find_handle(self, device_id: str) -> Any | None:
        try:
            return self._registry.find_handle(device_id)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _hardware_package(plan: Classification) -> dict[str, Any]:
        return {
            "rule": plan.rule,
            "cause": f"{plan.message}（规则 {plan.rule} 判定为硬件类问题）",
            "steps": [
                "确认故障设备并复核指标（重读设备快照）",
                "按知识库处理方法执行维护（如降载、更换备件、校准）",
                "回归验证：指标恢复后执行一次设备自检",
            ],
            "risk": "high",
            "verification": "重读该指标并确认回到正常区间",
        }

    async def _verify_device(self, handle: Any, rule: str) -> bool:
        info = await read_device_system(handle)
        if rule == "device_system:firmware-stale":
            fw = info.get("firmware_version")
            target = info.get("target_firmware_version")
            return bool(fw and target and str(fw) == str(target))
        if rule == "device_system:config-drift":
            return str(info.get("config_hash", "")) == str(info.get("expected_config_hash", ""))
        return info.get("agent_health") == "healthy"

    async def _verify_platform(self, action: dict[str, Any], executed: bool) -> bool:
        if not executed:
            return False
        name = (action.get("params") or {}).get("name", "")
        handle = self._find_handle(name) if name else None
        if handle is None:
            return True  # nothing inspectable; butler reported success
        return getattr(handle, "state", None) is None or handle.state.value == "available"

    async def _escalate(self, plan: Classification) -> None:
        try:
            await self._butler.execute_task("创建工单")
        except Exception:  # noqa: BLE001 - escalation is best effort
            pass

    async def heal(
        self,
        hit: RuleHit | dict,
        signals: dict[str, Any] | None = None,
        consent: str | bool | None = None,
    ) -> dict[str, Any]:
        signals = signals or {}
        plan = classify(hit, signals)
        base = {
            "kind": plan.kind,
            "rule": plan.rule,
            "device_id": plan.device_id,
            "message": plan.message,
        }

        # ------------------------------------------------- platform software
        if plan.kind == KIND_PLATFORM_SW:
            action = platform_actions(plan)[0]
            if action["risk"] == "high" and consent is None:
                return {**base, "need_consent": True, "action": action}
            if consent in ("deny", False):
                return {**base, "consent": "deny", "executed": False, "action": action}
            token = (
                consent
                if isinstance(consent, str) and consent not in ("allow", "deny", "")
                else None
            )
            try:
                res = await self._butler.execute_task(action["task"], confirm_token=token)
            except Exception as exc:  # noqa: BLE001 - degradation contract
                return {**base, "executed": False, "error": str(exc), "action": action}
            executed = bool(res.get("ok") is True or res.get("status") == "executed")
            verified = await self._verify_platform(action, executed)
            return {
                **base,
                "executed": executed,
                "verified": verified,
                "escalated": not verified,
                "action": action,
                "butler_response": res,
            }

        # --------------------------------------------------- device software
        if plan.kind == KIND_DEVICE_SW:
            handle = self._find_handle(plan.device_id)
            if handle is None:
                return {**base, "error": "device handle not found", "executed": False}
            tool, args = DEVICE_REPAIR_TOOLS.get(plan.rule, ("", {}))
            if consent is None:
                return {
                    **base,
                    "need_consent": True,
                    "repair": {"tool": tool, "rule": plan.rule},
                }
            if consent in ("deny", False):
                return {**base, "consent": "deny", "executed": False}
            try:
                await handle.call_tool(tool, args)
            except Exception as exc:  # noqa: BLE001
                return {**base, "executed": False, "error": str(exc), "consent": "allow"}
            verified = await self._verify_device(handle, plan.rule)
            escalated = False
            if not verified:
                escalated = True
                await self._escalate(plan)
            return {
                **base,
                "executed": True,
                "verified": verified,
                "escalated": escalated,
                "repair": {"tool": tool},
            }

        # ---------------------------------------------------------- hardware
        return {**base, "need_consent": False, "plan": self._hardware_package(plan)}
