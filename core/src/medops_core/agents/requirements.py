"""P6b: requirement-gathering FSM for the secretary agent.

Vague user input -> cycle of structured questions (deterministic bank;
LLM rewrites phrasing when attached) -> full prompt spec -> targeted
inspection via ``inspect_with_spec`` -> summary.

State machine: idle -> gathering -> spec_ready -> (execute) -> idle.
"""

from __future__ import annotations

from typing import Any

from medops_core.agents.inspector import InspectorAgent

_DEVICE_KEYWORDS: dict[str, str] = {
    "ct": "ct-sim-01",
    "ct机": "ct-sim-01",
    "计算机断层": "ct-sim-01",
    "dr": "dr-sim-01",
    "digital radiography": "dr-sim-01",
    "呼吸机": "ventilator-sim-01",
    "ventilator": "ventilator-sim-01",
    "心电图": "ecg-sim-01",
    "ecg": "ecg-sim-01",
}

_VAGUE_HINTS = ("有点问题", "不太对劲", "不对劲", "好像", "坏了", "不正常", "有点怪", "出问题", "故障了", "有问题")
_START_HINTS = ("帮我查", "检查一下", "看看", "排查", "折腾", "怎么了", "没事吧")

# (slot, question template) — per intent, at most three rounds.
_QUESTION_BANK: dict[str, list[tuple[str, str]]] = {
    "device_check": [
        ("devices", "涉及哪台设备？（如 ct / dr / 呼吸机，或直接给设备编号）"),
        ("symptoms", "请描述异常表现或症状，越具体越好。"),
        ("time", "大约什么时间开始的？影响大不大？"),
    ],
    "repair_guide": [
        ("devices", "需要维修指导的是哪台设备？"),
        ("symptoms", "故障现象是什么？"),
        ("mode", "希望以哪种方式处理？（A. 生成处置方案+工单；B. 逐步引导修复）"),
    ],
    "inspection": [
        ("devices", "巡检范围？（如全部 / ct / dr / 呼吸机）"),
        ("symptoms", "重点关注什么？"),
        ("frequency", "巡检频率有要求吗？（如一次性 / 定期）"),
    ],
    "report": [
        ("devices", "报告覆盖哪些设备？"),
        ("symptoms", "报告侧重什么时间范围或指标？"),
        ("urgency", "报告紧急程度？"),
    ],
}

_MARKER_WORDS = {"全部", "全部设备", "所有设备", "所有"}


def _parse_devices(text: str) -> list[str]:
    lowered = text.lower()
    if any(w in lowered for w in _MARKER_WORDS):
        return []
    found: list[str] = []
    for key, dev_id in _DEVICE_KEYWORDS.items():
        if key.lower() in lowered and dev_id not in found:
            found.append(dev_id)
    # direct sim-id match: ct-sim-01
    import re

    for m in re.findall(r"\b([a-zA-Z]+)-sim-\d+\b", lowered):
        dev_id = f"{m.lower()}-sim-01"
        if dev_id not in found:
            found.append(dev_id)
    return found


def _looks_vague(text: str, intent: str) -> bool:
    return any(h in text for h in _VAGUE_HINTS) or any(h in text for h in _START_HINTS)


_DEVICE_NAMES = {
    "ct-sim-01": "CT",
    "dr-sim-01": "DR",
    "ventilator-sim-01": "呼吸机",
    "ecg-sim-01": "心电图",
}


def synthesize_prompt(spec: dict) -> str:
    """The complete prompt handed to the inspection agent (P6c prompt-builder
    shape: role / context / action / output)."""
    devices = "、".join(
        _DEVICE_NAMES.get(d, d) for d in (spec.get("devices", []) or [])
    )
    symptoms = "；".join(spec.get("symptoms", []) or []) or "待检"
    return (
        f"角色：医疗设备运维巡检 Agent\n"
        f"任务：针对 {devices or '全部设备'} 的异常线索执行一次定向巡检并输出发现\n"
        f"线索/症状：{symptoms}\n"
        f"范围：设备 {spec.get('devices', []) or 'all'}；关注点 {spec.get('urgency', '常规')}\n"
        f"输出：每台设备的异常指标、可能原因（引用知识库）、处置建议类别（平台软件/设备软件/硬件）\n"
    )


class RequirementFSM:
    """One conversation's requirement state, owned by the secretary agent."""

    def __init__(self, llm: Any = None, inspector: InspectorAgent | None = None) -> None:
        self._llm = llm
        self._inspector = inspector
        self.state = "idle"  # idle | gathering | spec_ready
        self.intent = "device_check"
        self.fields: dict[str, list[str] | str] = {"devices": [], "symptoms": []}
        self._question_index = 0
        self._last_answer = ""
        self._result: list[dict[str, Any]] = []

    # ------------------------------------------------------------ lifecycle
    def maybe_start(self, text: str) -> bool:
        """Start a gathering session when the input is vague/requesty."""
        intent = "device_check"
        if "修" in text or "方案" in text or "处理" in text:
            intent = "repair_guide"
        elif "报告" in text:
            intent = "report"
        elif "巡检" in text:
            intent = "inspection"
        if _looks_vague(text, intent):
            self.state = "gathering"
            self.intent = intent
            self.fields = {"devices": [], "symptoms": []}
            self._question_index = 0
            return True
        return False

    def is_active(self) -> bool:
        return self.state in ("gathering", "spec_ready")

    def _bank(self) -> list[tuple[str, str]]:
        return _QUESTION_BANK.get(self.intent, _QUESTION_BANK["device_check"])

    def next_question(self) -> str | None:  # noqa: PLR5501
        bank = self._bank()
        if self._question_index >= len(bank):
            return None
        _, question = bank[self._question_index]
        return question

    async def ask(self) -> str | None:
        """Next question, LLM-phrased when a real LLM is attached (async-safe)."""
        question = self.next_question()
        if question is None:
            return None
        if self._llm is None or getattr(self._llm, "provider_name", "") in ("fake", ""):
            return question
        try:
            resp = await self._llm.chat(  # noqa: ANN401 - LLMClient interface
                [
                    {
                        "role": "user",
                        "content": (
                            f"用一句话用中文向用户追问：{question}"
                            f"（当前已收集：{self.fields}）"
                        ),
                    }
                ]
            )
            text = getattr(resp, "text", None)
            return (text or question).strip() or question
        except Exception:  # noqa: BLE001 - deterministic fallback
            return question

    def consume(self, text: str) -> str | None:
        """Record the user's answer to the current question.

        Returns the next question, or None when the spec is complete.
        """
        bank = self._bank()
        slot, _ = bank[self._question_index]
        parsed = _parse_devices(text) if slot == "devices" else [text.strip()]
        if slot == "devices":
            if parsed:
                merged = list(dict.fromkeys(
                    self.fields["devices"] + parsed  # type: ignore[operator]
                ))
                self.fields["devices"] = merged
            elif any(w in text for w in _MARKER_WORDS):
                self.fields["devices"] = ["all"]
        elif slot == "symptoms":
            cur = self.fields.get("symptoms") or []
            self.fields["symptoms"] = cur + parsed  # type: ignore[operator,union-attr]
        else:
            self.fields.setdefault(slot, text.strip())
        self._question_index += 1
        self._last_answer = text
        if self._question_index >= len(bank) or self._is_complete():
            self.state = "spec_ready"
            return None
        return self.next_question()

    def _is_complete(self) -> bool:
        devices = self.fields.get("devices")
        symptoms = self.fields.get("symptoms")
        return bool(devices and symptoms)

    def synthesize(self) -> dict:
        if self.intent == "inspection" and self.fields.get("devices") == ["all"]:
            pass
        return {
            "intent": self.intent,
            "devices": self.fields.get("devices") or [],
            "symptoms": self.fields.get("symptoms") or [],
            "urgency": self.fields.get("urgency", "常规"),
            "output_format": "anomalies + kb causes + remediation kinds",
            "prompt": synthesize_prompt(
                {
                    "intent": self.intent,
                    "devices": self.fields.get("devices") or [],
                    "symptoms": self.fields.get("symptoms") or [],
                    "urgency": self.fields.get("urgency", "常规"),
                }
            ),
            "source": "secretary_requirement",
        }

    def mode_choice(self) -> str:
        mode = self.fields.get("mode", "")
        return "guided" if "b" in str(mode).lower() or "引导" in str(mode) else "package"

    # ------------------------------------------------------------- execute
    async def execute(self, inspector) -> list[dict[str, Any]]:
        spec = self.synthesize()
        if self._inspector is not None:
            inspector = self._inspector
        if inspector is None:
            self._result = []
            return []
        try:
            result = await inspector.inspect_with_spec(spec)
            self._result = getattr(result, "anomalies", result) or []
        except Exception:  # noqa: BLE001 - degrading to empty findings
            self._result = []
        self.state = "idle"
        return self._result

    def summarize(self) -> str:
        if not self._result:
            return "定向巡检完成：未发现异常。建议保持常规巡检节奏。"
        lines = ["定向巡检完成，发现以下异常："]
        for item in self._result:
            lines.append(
                f"- {item.get('device_id', '?')}: {item.get('message', '')}"
                f"（规则 {item.get('rule', '?')}）"
            )
        lines.append("处置建议：请查看告警处置面板（平台软件自动修复 / 设备软件需确认 / 硬件生成方案）。")
        return "\n".join(lines)
