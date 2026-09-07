"""Golden scenarios for agent evaluation (P2-6).

Each scenario: a question, the expected intent/tool routing, and an
assertion rule evaluated against the agent's answer/trajectory. The
suite runs fully offline with FakeLLM (deterministic); `run_eval.py`
optionally scores answers with real LLMs / DeepEval metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GoldenScenario:
    sid: str
    category: str  # status_query | fault_diagnosis | ledger | chitchat
    question: str
    expected_tool: str | None
    expected_server: str | None
    # keywords that must appear in the answer (case-insensitive)
    answer_must_contain: list[str] = field(default_factory=list)


def _g(sid: str, category: str, question: str, tool: str | None, server: str | None,
       must: list[str] | None = None) -> GoldenScenario:
    return GoldenScenario(sid, category, question, tool, server, must or [])


SCENARIOS: list[GoldenScenario] = [
    # ---- status queries (10) ----
    _g("s01", "status_query", "3号CT球管温度多少", "get_tube_stats", "ct"),
    _g("s02", "status_query", "CT 曝光次数是多少", "get_tube_stats", "ct"),
    _g("s03", "status_query", "呼吸机氧浓度现在多少", "get_realtime_params", "ventilator"),
    _g("s04", "status_query", "呼吸机潮气量和气道压力怎么样", "get_realtime_params", "ventilator"),
    _g("s05", "status_query", "DR 探测器温度多少", "get_detector_temp", "dr"),
    _g("s06", "status_query", "DR 发生器状态正常吗", "get_detector_temp", "dr"),
    _g("s07", "status_query", "心电波形质量如何", "get_waveform_quality", "ecg"),
    _g("s08", "status_query", "CT 的 DICOM 目录检查一下", "check_dicom_dir", "ct"),
    _g("s09", "status_query", "PACS 连接正常吗", "check_pacs_connectivity", "ct"),
    _g("s10", "status_query", "呼吸机自检一下", "run_self_test", "ventilator"),
    # ---- fault diagnosis (12, mapped to the 9 scenarios + variants) ----
    _g("d01", "fault_diagnosis", "CT 球管过热报警了怎么回事", "get_tube_stats", "ct", ["球管"]),
    _g("d02", "fault_diagnosis", "CT 报错 tube temperature too high", "get_tube_stats", "ct"),
    _g("d03", "fault_diagnosis", "呼吸机低氧浓度报警怎么处理",
       "search_knowledge", None, ["氧"]),  # P4c: 怎么处理 -> KB
    _g("d04", "fault_diagnosis", "呼吸机气道压力低是怎么回事", "get_realtime_params", "ventilator"),
    _g("d05", "fault_diagnosis", "DR 图像噪声变大什么原因", "search_knowledge", None),  # 原因 -> KB
    _g("d06", "fault_diagnosis", "DR 发生器 kV 超差", "get_detector_temp", "dr"),
    _g("d07", "fault_diagnosis", "心电导联脱落了怎么办", "search_knowledge", None,
       ["导联"]),  # 怎么办 -> KB
    _g("d08", "fault_diagnosis", "心电 SNR 异常怎么排查", "get_waveform_quality", "ecg"),
    _g("d09", "fault_diagnosis", "CT 连不上 PACS 了", "check_pacs_connectivity", "ct"),
    _g("d10", "fault_diagnosis", "磁盘空间不足影响拍片吗", "get_detector_temp", "dr"),
    _g("d11", "fault_diagnosis", "呼吸机自检失败说明什么", "run_self_test", "ventilator"),
    _g("d12", "fault_diagnosis", "球管温度持续上升要停机吗", "get_tube_stats", "ct"),
    # ---- ledger / maintenance (5) ----
    _g("l01", "ledger", "现在有哪些告警", "query_alerts", "maintenance-db"),
    _g("l02", "ledger", "最近的维修工单有哪些", "query_alerts", "maintenance-db"),
    _g("l03", "maintenance", "哪些设备维保快到期了", "get_maintenance_due", "maintenance-db"),
    _g("l04", "maintenance", "CT 什么时候该做保养", "get_maintenance_due", "maintenance-db"),
    _g("l05", "ledger", "查一下设备台账", "query_alerts", "maintenance-db"),
    # ---- chitchat / fallback (3) ----
    _g("c01", "chitchat", "你好", None, None),
    _g("c02", "chitchat", "你能做什么", None, None),
    _g("c03", "chitchat", "帮我写一首诗", None, None),
]


def tool_hit(used_tools: list[str], expected: str | None) -> bool:
    if expected is None:
        return True  # chitchat: no tool expected
    return expected in used_tools
