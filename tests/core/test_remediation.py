"""P6a: fault triage & remediation — three-channel classification
(platform software auto / device software user-consent / hardware plan),
verify & escalation, REST surface."""

from __future__ import annotations

from medops_core import remediation
from medops_core.log_pipeline.rules import RuleHit


def _hit(rule: str, metric: str | None = None, value: float | None = None,
         message: str = "anomaly", device: str = "ct-sim-01") -> RuleHit:
    return RuleHit(
        device_id=device,
        level="warning",
        message=message,
        rule=rule,
        ts="2026-09-09T00:00:00+00:00",
    )


def _signals(**kw):
    return kw


# ------------------------------------------------------------ classification
def test_classify_platform_sw_from_mcp_signal():
    hit = _hit("mcp:unavailable", message="MCP server ct-01 unavailable")
    plan = remediation.classify(hit, signals=_signals(mcp_unavailable=["ct-01"]))
    assert plan.kind == "platform_sw"
    assert plan.rule == "mcp:unavailable"


def test_classify_device_sw_firmware_stale():
    hit = _hit("device_system:firmware-stale", metric="firmware_version", value=1.0,
               message="firmware 1.0.0 < required 1.2.0")
    plan = remediation.classify(hit, signals={})
    assert plan.kind == "device_sw"


def test_classify_device_sw_agent_stuck():
    hit = _hit("device_system:agent-stuck", message="agent_health=stuck")
    assert remediation.classify(hit, signals={}).kind == "device_sw"


def test_classify_device_sw_config_drift():
    hit = _hit("device_system:config-drift", message="config_hash mismatch")
    assert remediation.classify(hit, signals={}).kind == "device_sw"


def test_classify_hardware_metrics_default():
    hit = _hit("threshold:tube_temp:error", metric="tube_temp", value=78.0)
    plan = remediation.classify(hit, signals={})
    assert plan.kind == "hardware"


def test_classify_llm_check_confirms_rules():
    class LLM:
        provider_name = "stub"

        def check_classification(self, evidence: dict) -> dict:
            return {"kind": "hardware", "reason": "llm says hardware"}

    hit = _hit("threshold:tube_temp:error", metric="tube_temp", value=78.0)
    plan = remediation.classify(hit, signals={}, llm=LLM())
    assert plan.kind == "hardware"
    assert plan.llm_verified is True


def test_classify_llm_unavailable_is_rules_only():
    hit = _hit("device_system:agent-stuck")
    plan = remediation.classify(hit, signals={})
    assert plan.llm_verified is False


# ------------------------------------------------------- platform mapping
def test_platform_map_mcp_unavailable_register_server():
    hit = _hit("mcp:unavailable", message="MCP server ct-01 unavailable")
    plan = remediation.classify(hit, signals=_signals(mcp_unavailable=["ct-01"]))
    actions = remediation.platform_actions(plan)
    assert any(a["op"] == "register_mcp_server" and a["params"].get("name") == "ct-01"
               for a in actions)


def test_platform_map_endpoint_toggle():
    hit = _hit("mcp:unavailable", message="endpoint smoke-his is down")
    plan = remediation.classify(hit, signals=_signals(mcp_unavailable=[]))
    actions = remediation.platform_actions(plan)
    assert any("toggle_endpoint" in a["op"] for a in actions)


def test_platform_map_cleanup_data():
    hit = _hit("platform:volume", message="平台日志存储不足，建议清理数据")
    plan = remediation.classify(hit, signals={})
    actions = remediation.platform_actions(plan)
    assert any(a["op"] == "cleanup_data" for a in actions)


def test_platform_map_fallback_work_order():
    hit = _hit("mcp:unavailable", message="mystery")
    plan = remediation.classify(hit, signals={})
    actions = remediation.platform_actions(plan)
    assert any(a["op"] == "create_work_order" for a in actions)


# ---------------------------------------------- device software consents
class _FakeHandle:
    def __init__(self, name="ct-01"):
        self.config_name = name
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool: str, args: dict | None = None) -> dict:
        self.calls.append((tool, args or {}))
        return {"ok": True}


class _FakeRegistry:
    def __init__(self, handle):
        self.handles = {"ct": handle}
        self._name_key = handle.config_name

    def find_handle(self, name: str):
        return self.handles.get(name.split("-")[0], None)


class _FakeButler:
    def __init__(self):
        self.executed: list[str] = []
        self._create = None

    async def execute_task(self, task: str, confirm_token: str | None = None) -> dict:
        self.executed.append(task)
        return {"ok": True}


async def test_device_sw_requires_consent_first():
    handle = _FakeHandle()
    reg = _FakeRegistry(handle)
    butler = _FakeButler()
    service = remediation.RemediationService(reg, butler)
    hit = _hit("device_system:agent-stuck")
    outcome = await service.heal(hit, signals={})
    assert outcome["need_consent"] is True
    assert handle.calls == []  # nothing executed without consent


async def test_device_sw_consent_executes_and_verifies(monkeypatch):
    handle = _FakeHandle()
    reg = _FakeRegistry(handle)
    butler = _FakeButler()
    service = remediation.RemediationService(reg, butler)

    # post-repair snapshot shows the agent healthy (contract: returns the block)
    async def _reader(_h: Any) -> dict:
        return {"agent_health": "healthy"}

    monkeypatch.setattr(remediation, "read_device_system", _reader)

    hit = _hit("device_system:agent-stuck")
    outcome = await service.heal(hit, signals={}, consent="allow")
    assert outcome["executed"] is True
    assert any("restart_device_agent" in call for call, _ in handle.calls)
    assert outcome["verified"] is True


async def test_device_sw_deny_executes_nothing():
    handle = _FakeHandle()
    service = remediation.RemediationService(_FakeRegistry(handle), _FakeButler())
    hit = _hit("device_system:agent-stuck")
    outcome = await service.heal(hit, signals={}, consent="deny")
    assert outcome["executed"] is False
    assert outcome["consent"] == "deny"
    assert handle.calls == []


async def test_hardware_plan_package_created():
    service = remediation.RemediationService(_FakeRegistry(_FakeHandle()), _FakeButler())
    hit = _hit("threshold:tube_temp:error", metric="tube_temp", value=78.0)
    outcome = await service.heal(hit, signals={})
    assert outcome["kind"] == "hardware"
    assert outcome["need_consent"] is False
    pkg = outcome["plan"]
    for key in ("cause", "steps", "risk", "verification"):
        assert key in pkg


async def test_verify_failure_escalates_to_work_order():
    handle = _FakeHandle()
    butler = _FakeButler()
    service = remediation.RemediationService(_FakeRegistry(handle), butler)
    hit = _hit("device_system:agent-stuck")

    class _Broken:
        def __init__(self):
            self.calls = []

        async def call_tool(self, tool, args=None):
            self.calls.append(tool)
            return {"device_system": {"agent_health": "stuck"}}  # still stuck

    handle.call_tool = _Broken().call_tool  # type: ignore[assignment]
    outcome = await service.heal(hit, signals={}, consent="allow")
    assert outcome["verified"] is False
    assert outcome["escalated"] is True
    assert any("工单" in t or "work order" in t.lower() for t in butler.executed)
