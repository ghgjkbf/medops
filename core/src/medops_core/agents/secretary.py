"""Secretary agent: Q&A over device data via MCP tools (design §5.2).

Pipeline: rule-based intent classification -> tool calls via MCPRegistry
-> LLM composes the answer (with tool context) -> full trajectory
returned and persisted (chat_session / chat_message.tool_trace).
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from medops_core.agents.base import BaseAgent, ToolCall
from medops_core.mcp_client.registry import MCPRegistry
from medops_core.models import ChatMessage, ChatSession

# Intent rules: (pattern, intent, tool_hint) — first match wins. Rules keep
# tool selection deterministic; the LLM only phrases the final answer.
_INTENT_RULES: list[tuple[str, str, str | None]] = [
    # dr (探测器/发生器 must precede generic 温度)
    (r"探测器|发生器|kV|kv|磁盘|存储|图像噪声|噪声", "status_query", "get_detector_temp"),
    # ct
    (r"温度|球管|曝光|tube", "status_query", "get_tube_stats"),
    (r"DICOM|影像目录", "status_query", "check_dicom_dir"),
    (r"PACS|pacs|连不上", "status_query", "check_pacs_connectivity"),
    # ventilator
    (r"自检", "status_query", "run_self_test"),
    (r"氧浓度|潮气量|气道|通气|呼吸机", "status_query", "get_realtime_params"),
    # ecg
    (r"心电|波形|导联|SNR|snr", "status_query", "get_waveform_quality"),
    # ledger / maintenance
    (r"告警|预警", "ledger", "query_alerts"),
    (r"工单|维修", "ledger", "query_alerts"),
    (r"保养|维护|维保|到期", "maintenance", "get_maintenance_due"),
    (r"台账|设备列表", "ledger", "query_alerts"),
    (r"你好|帮助|能做什么|hello|help", "chitchat", None),
]


@dataclass
class Intent:
    name: str
    tool: str | None
    server: str | None


def classify_intent(question: str) -> Intent:
    """Deterministic intent classification (rule table, not LLM)."""
    q = question.lower()
    for pattern, intent, tool in _INTENT_RULES:
        if re.search(pattern, q, re.IGNORECASE):
            server = None
            if tool:
                server = _server_for_tool(tool)
            return Intent(name=intent, tool=tool, server=server)
    return Intent(name="unknown", tool=None, server=None)


def _server_for_tool(tool: str) -> str | None:
    for server, tools in {
        "ct": ["get_tube_stats", "check_dicom_dir", "check_pacs_connectivity"],
        "ventilator": ["get_realtime_params", "run_self_test"],
        "dr": ["get_detector_temp", "check_generator_status"],
        "ecg": ["get_waveform_quality", "check_export_files"],
        "maintenance-db": [
            "query_alerts", "get_maintenance_due", "query_devices",
            "create_work_order", "update_work_order", "add_repair_record",
        ],
    }.items():
        if tool in tools:
            return server
    return None


class SecretaryAgent(BaseAgent):
    name = "secretary"

    def __init__(self, llm, registry: MCPRegistry, session=None) -> None:  # noqa: ANN001
        super().__init__(llm, tools={})
        self._registry = registry
        self._session = session

    async def plan(self, user_input: str) -> str:  # noqa: C901 - intent dispatch
        intent = classify_intent(user_input)
        tool_payload: dict[str, Any] | None = None

        if intent.tool and intent.server:
            try:
                handle = self._registry.get(intent.server)
                if intent.tool in handle.tools:
                    started = time.perf_counter()
                    raw = await handle.call_tool(intent.tool)
                    latency = int((time.perf_counter() - started) * 1000)
                    # mcp-sdk note §4: dict payloads may arrive as JSON text
                    if isinstance(raw, str):
                        import json  # noqa: PLC0415

                        try:
                            raw = json.loads(raw)
                        except json.JSONDecodeError:
                            pass
                    tool_payload = raw if isinstance(raw, dict) else {"result": raw}
                    self._trajectory.append(
                        ToolCall(
                            name=intent.tool,
                            args={"server": intent.server},
                            result=tool_payload,
                            latency_ms=latency,
                        )
                    )
            except (KeyError, RuntimeError) as exc:
                self._trajectory.append(
                    ToolCall(
                        name=intent.tool or "?",
                        args={"server": intent.server},
                        result=None,
                        latency_ms=0,
                        ok=False,
                        error=str(exc),
                    )
                )
                tool_payload = None

        context_lines: list[str] = []
        if tool_payload:
            for key in ("status", "metrics", "params", "detector_temp", "waveform_snr"):
                if key in tool_payload:
                    context_lines.append(f"{key}: {tool_payload[key]}")
            if not context_lines:
                context_lines.append(str(tool_payload)[:300])

        system = (
            "你是医疗器械运维助手。基于给定的工具数据用中文简洁回答，"
            "不要编造数据；若工具数据为空请如实说明。"
        )
        user = (
            f"用户问题：{user_input}\n"
            + (f"工具数据：{'；'.join(context_lines)}" if context_lines else "（无工具数据）")
        )
        answer, _provider = await self.ask_llm(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        await self._persist(user_input, answer, intent)
        return answer

    # ---------------------------------------------------------------- persist
    async def _persist(self, question: str, answer: str, intent: Intent) -> None:
        if self._session is None:
            return
        trajectory = [
            {
                "tool": c.name,
                "args": c.args,
                "result": c.result if c.ok else None,
                "error": c.error,
                "latency_ms": c.latency_ms,
            }
            for c in self._trajectory
        ]
        self._session.add(ChatSession(session_key=f"s-{datetime.now(UTC).timestamp()}"))
        await self._session.flush()
        self._session.add(
            ChatMessage(session_id=1, role="user", content=question)
        )
        self._session.add(
            ChatMessage(
                session_id=1,
                role="assistant",
                content=answer,
                tool_trace=trajectory,
            )
        )
        commit = self._session.commit()
        if inspect.isawaitable(commit):
            await commit
