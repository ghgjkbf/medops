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
from functools import partial
from typing import Any

from medops_core import knowledge
from medops_core.agents.base import BaseAgent, ToolCall
from medops_core.mcp_client.registry import MCPRegistry
from medops_core.models import ChatMessage, ChatSession
from medops_core.reporting import device_fault_report

# Intent rules: (pattern, intent, tool_hint) — first match wins. Rules keep
# tool selection deterministic; the LLM only phrases the final answer.
# KB/report rules sit on top: "球管过热怎么处理" must hit knowledge, not the
# status query for 球管.
_INTENT_RULES: list[tuple[str, str, str | None]] = [
    # fault report (before ledger/status rules: "告警报告" -> report)
    (r"故障报告|设备报告|健康报告", "report", "get_fault_report"),
    # knowledge base (cause / handling)
    (
        r"怎么处理|处理方法|处理步骤|故障原因|什么原因|原因|怎么办|排除|知识",
        "knowledge", "search_knowledge",
    ),
    # butler (management operations): action verbs beat generic ledger nouns
    (
        r"(?:把|将)\s*工单|关闭\s*工单|工单\s*#?\s*\d+\s*转|创建工单|新建工单"
        r"|删除|清理|清空|移除|触发巡检|立即巡检|巡检一次"
        r"|生成\s*(?:平台\s*)?报告|注册\s*(?:MCP\s*)?服务|停用\s*端点|启用\s*端点",
        "butler", "butler",
    ),
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

    def __init__(  # noqa: ANN001 - same style as base
        self, llm, registry: MCPRegistry, session=None, db_factory=None, butler=None
    ) -> None:
        super().__init__(llm, tools={})
        self._registry = registry
        self._session = session
        self._db_factory = db_factory
        self._butler = butler
        if db_factory is not None:
            # builtin (non-MCP) tools: knowledge retrieval + fault report
            self.register_tool(
                "search_knowledge",
                partial(knowledge.search_documents, db_factory),
            )
            self.register_tool(
                "get_fault_report", partial(device_fault_report, db_factory)
            )

    async def _run_builtin(self, tool: str, args: dict) -> object | None:
        """Execute a registered builtin (possibly async) tool with trajectory."""
        started = time.perf_counter()
        try:
            result = self.tools[tool](**args)
            if inspect.isawaitable(result):
                result = await result
            self._trajectory.append(
                ToolCall(
                    name=tool,
                    args=args,
                    result=result,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            return result
        except Exception as exc:  # noqa: BLE001 - tool errors are recorded
            self._trajectory.append(
                ToolCall(
                    name=tool,
                    args=args,
                    result=None,
                    latency_ms=0,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            return None

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

        builtin_payload: object | None = None
        if intent.tool and intent.server is None and intent.tool in self.tools:
            args: dict[str, Any] = (
                {"query": user_input}
                if intent.tool == "search_knowledge"
                else {"question": user_input}
            )
            builtin_payload = await self._run_builtin(intent.tool, args)

        butler_result: dict[str, Any] | None = None
        if intent.name == "butler" and self._butler is not None:
            started = time.perf_counter()
            butler_result = await self._butler.execute_task(user_input)
            self._trajectory.append(
                ToolCall(
                    name="butler",
                    args={"task": user_input},
                    result=butler_result,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            )

        context_lines: list[str] = []
        if tool_payload:
            for key in ("status", "metrics", "params", "detector_temp", "waveform_snr"):
                if key in tool_payload:
                    context_lines.append(f"{key}: {tool_payload[key]}")
            if not context_lines:
                context_lines.append(str(tool_payload)[:300])
        if builtin_payload is not None:
            if intent.tool == "search_knowledge":
                hits = builtin_payload if isinstance(builtin_payload, list) else []
                for i, hit in enumerate(hits, 1):
                    context_lines.append(
                        f"知识库[{i}] {hit['title']}：{hit['content'][:150]}"
                    )
            elif isinstance(builtin_payload, dict):  # fault report
                context_lines.append(str(builtin_payload.get("markdown", ""))[:800])
        if isinstance(butler_result, dict):
            if butler_result.get("status") == "pending_confirmation":
                context_lines.append(
                    f"操作待确认：{butler_result.get('preview')}（{butler_result.get('hint')}）"
                )
            elif butler_result.get("status") == "executed":
                r = butler_result.get("result", {})
                context_lines.append(
                    f"管家已执行 {butler_result.get('operation')}："
                    + (r.get("error") or str({k: v for k, v in r.items() if k != "markdown"})[:200])
                )

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
