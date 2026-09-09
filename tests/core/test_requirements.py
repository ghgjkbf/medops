"""P6b: secretary requirement-gathering FSM — vague input → structured
questions (deterministic bank, LLM phrasing when available) → full prompt
spec → targeted inspection (inspect_with_spec) → summary."""

from __future__ import annotations

from medops_core.agents.inspector import InspectorAgent
from medops_core.agents.requirements import RequirementFSM, synthesize_prompt


class _FakeHandle:
    def __init__(self, name: str, tools: list[str]):
        self.config_name = name
        self.state = type("S", (), {"value": "available"})()
        self.tools = tools
        self.calls: list[str] = []
        self.config = type("C", (), {"name": name})()

    async def call_tool(self, tool: str, args: dict | None = None) -> dict:
        self.calls.append(tool)
        return {
            "device_id": f"{self.config_name.split('-')[0]}-sim-01",
            "metrics": {"tube_temp": 78.0},
            "device_system": {"agent_health": "healthy"},
        }


class _FakeRegistry:
    def __init__(self, handles: list):
        self.handles = {h.config_name.split("-")[0]: h for h in handles}

    def find_handle(self, device_id: str):
        return self.handles.get(device_id.split("-")[0])


class _FakeLLM:
    provider_name = "fake"


def make_fsm(llm=None):
    return RequirementFSM(llm or _FakeLLM())


# -------------------------------------------------------------- FSM basics
def test_vague_input_starts_gathering():
    fsm = make_fsm()
    assert fsm.maybe_start("感觉设备好像有点问题") is True
    assert fsm.state == "gathering"
    q = fsm.next_question()
    assert q is not None and "设备" in q


def test_clear_input_does_not_start():
    fsm = make_fsm()
    assert fsm.maybe_start("如何查看当前球管温度") is False
    assert fsm.state == "idle"


def test_answers_advance_fields():
    fsm = make_fsm()
    fsm.maybe_start("设备有点问题")
    fsm.consume("ct 设备")
    assert fsm.fields["devices"] == ["ct-sim-01"]
    q = fsm.consume("球管温度很高，已经超过 80")
    assert q is None  # spec complete -> ready
    assert fsm.state == "spec_ready"
    spec = fsm.synthesize()
    assert "ct-sim-01" in spec["devices"]
    assert any("球管" in s for s in spec["symptoms"])


def test_spec_prompt_text_contains_context():
    fsm = make_fsm()
    fsm.maybe_start("设备有问题")
    fsm.consume("呼吸机")
    fsm.consume("氧浓度不稳定")
    prompt = synthesize_prompt(fsm.synthesize())
    for token in ("呼吸机", "氧浓度不稳定", "巡检", "输出"):
        assert token in prompt


def test_force_synth_after_three_rounds():
    fsm = make_fsm()
    fsm.maybe_start("有问题")
    fsm.consume("不知道哪台")
    fsm.consume("不知道什么症状")
    fsm.consume("就是坏了")
    assert fsm.state == "spec_ready"  # forced after 3 rounds
    spec = fsm.synthesize()
    assert "devices" in spec


def test_llm_question_hook_when_available():
    class LLMWithQuestions(_FakeLLM):
        provider_name = "deepseek"

        def __init__(self):
            self.calls = 0

        async def chat(self, messages):  # noqa: ANN001
            self.calls += 1
            return type(
                "Resp", (), {"text": "请告诉我是哪台设备呢？", "provider_used": "x"}
            )()

    import asyncio

    llm = LLMWithQuestions()
    fsm = make_fsm(llm)
    fsm.maybe_start("不太对劲")
    q = asyncio.run(fsm.ask())
    assert llm.calls >= 1 and "设备" in q


class _FakeInspector:
    def __init__(self):
        self.specs = []

    async def inspect_with_spec(self, spec: dict):  # noqa: ANN001
        self.specs.append(spec)
        return [
            {"device_id": "ct-sim-01", "rule": "threshold:tube_temp:error", "message": "tube_temp=78"}
        ]


async def test_full_happy_path_to_inspection():
    fsm = make_fsm()
    fsm.maybe_start("感觉设备有点不对劲")
    fsm.consume("ct 设备")
    fsm.consume("球管温度高")
    inspector = _FakeInspector()
    await fsm.execute(inspector)
    assert inspector.specs and inspector.specs[0]["devices"] == ["ct-sim-01"]
    summary = fsm.summarize()
    assert "ct-sim-01" in summary and "异常" in summary
    assert fsm.state == "idle"  # reset after completion


def test_repair_intent_question_set():
    fsm = make_fsm()
    fsm.maybe_start("帮我看看设备坏了怎么修")
    assert fsm.intent == "repair_guide"
    questions = [q for _, q in fsm._bank()]
    assert any("设备" in q for q in questions)
    assert any("修复" in q or "方案" in q for q in questions)


# ------------------------------------------------- inspector targeted run
async def test_inspect_with_spec_targets_only_requested_device(monkeypatch):
    ct = _FakeHandle("ct-01", ["get_tube_stats"])
    dr = _FakeHandle("dr-01", ["get_detector_temp"])

    async def _no_store(self, hits):  # noqa: ANN001
        return []

    monkeypatch.setattr(InspectorAgent, "_store_alerts", _no_store)
    inspector = InspectorAgent(_FakeLLM(), _FakeRegistry([ct, dr]), None)
    result = await inspector.inspect_with_spec({"devices": ["ct-sim-01"]})
    assert result.checked_servers == ["ct-01"]
    assert "ct-01.get_tube_stats" in result.tool_results or result.tool_results
    assert "dr-01" not in result.checked_servers


async def test_inspect_with_spec_unknown_device_empty(monkeypatch):
    async def _no_store(self, hits):  # noqa: ANN001
        return []

    monkeypatch.setattr(InspectorAgent, "_store_alerts", _no_store)
    inspector = InspectorAgent(_FakeLLM(), _FakeRegistry([_FakeHandle("ct-01", ["get_tube_stats"])]), None)
    result = await inspector.inspect_with_spec({"devices": ["zzz-sim-01"]})
    assert result.checked_servers == []
    assert result.anomalies == []
