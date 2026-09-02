"""Agent base: LLM injection, tool registry, trajectory recording.

Tools are callables registered per-agent; each call is recorded into the
trajectory (name / args / result / latency) which lands in
chat_message.tool_trace for auditability and the P3 UI.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from medops_core.agents.llm import FakeLLM, LLMClient, LLMResult, Message


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: Any
    latency_ms: int
    ok: bool = True
    error: str | None = None


@dataclass
class AgentResult:
    answer: str
    tool_trajectory: list[ToolCall] = field(default_factory=list)
    provider_used: str = ""
    latency_ms: int = 0

    def trajectory_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "tool": c.name,
                "args": c.args,
                "result": c.result if c.ok else None,
                "error": c.error,
                "latency_ms": c.latency_ms,
            }
            for c in self.tool_trajectory
        ]


class BaseAgent:
    """Common plumbing for inspector / secretary agents.

    Subclasses provide `system_prompt` and `plan()` — how they map input to
    tool calls. The LLM is injected (FakeLLM in tests); tool execution goes
    through `call_tool` which records the trajectory.
    """

    name: str = "base"

    def __init__(
        self,
        llm: LLMClient | FakeLLM,
        tools: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        self.llm = llm
        self.tools: dict[str, Callable[..., Any]] = tools or {}
        self._trajectory: list[ToolCall] = []

    # ------------------------------------------------------------------ tools
    def register_tool(self, name: str, fn: Callable[..., Any]) -> None:
        self.tools[name] = fn

    def call_tool(self, name: str, args: dict[str, Any] | None = None) -> Any:
        """Execute a registered tool, recording latency/errors into trajectory."""
        fn = self.tools.get(name)
        started = time.perf_counter()
        if fn is None:
            call = ToolCall(
                name=name,
                args=args or {},
                result=None,
                latency_ms=0,
                ok=False,
                error=f"unknown tool {name!r}",
            )
            self._trajectory.append(call)
            raise KeyError(call.error)
        try:
            result = fn(**(args or {}))
            latency = int((time.perf_counter() - started) * 1000)
            call = ToolCall(name=name, args=args or {}, result=result, latency_ms=latency)
        except Exception as exc:  # noqa: BLE001 - tool errors are recorded, not raised
            latency = int((time.perf_counter() - started) * 1000)
            call = ToolCall(
                name=name,
                args=args or {},
                result=None,
                latency_ms=latency,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        self._trajectory.append(call)
        return call.result

    # ------------------------------------------------------------------- llm
    async def ask_llm(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        """LLM call helper; returns (text, provider_used)."""
        result: LLMResult = await self.llm.chat(
            [Message(**m) for m in messages]  # type: ignore[misc]
        )
        return result.text, result.provider_used

    # ------------------------------------------------------------------ run
    async def run(self, user_input: str) -> AgentResult:
        """Template method: subclass hooks build the answer; trajectory recorded."""
        self._trajectory = []
        started = time.perf_counter()
        answer = await self.plan(user_input)
        return AgentResult(
            answer=answer,
            tool_trajectory=self._trajectory,
            provider_used=getattr(self.llm, "provider_name", "llm"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def plan(self, user_input: str) -> str:  # pragma: no cover - abstract
        raise NotImplementedError
