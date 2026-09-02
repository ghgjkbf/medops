"""P2-2: Agent base tests (FakeLLM; LangGraph availability probe)."""

from __future__ import annotations

import pytest
from medops_core.agents.base import AgentResult, BaseAgent
from medops_core.agents.llm import FakeLLM


class EchoAgent(BaseAgent):
    """Test agent: one tool call, then an LLM answer."""

    name = "echo"

    def __init__(self, llm, tools):  # noqa: ANN001
        super().__init__(llm, tools)

    async def plan(self, user_input: str) -> str:
        value = self.call_tool("echo", {"text": user_input})
        answer, provider = await self.ask_llm(
            [
                {"role": "system", "content": "echo agent"},
                {"role": "user", "content": f"tool said: {value}"},
            ]
        )
        self.provider_used = provider
        return answer


def _agent() -> EchoAgent:
    fake = FakeLLM(text="llm answered")
    return EchoAgent(fake, tools={"echo": lambda text: f"tool:{text}"})


async def test_run_returns_answer_and_trajectory() -> None:
    agent = _agent()
    result = await agent.run("hello")
    assert isinstance(result, AgentResult)
    assert result.answer == "llm answered"
    assert result.provider_used == "fake"
    assert result.latency_ms >= 0


async def test_trajectory_records_tool_call() -> None:
    agent = _agent()
    result = await agent.run("hello")
    assert len(result.tool_trajectory) == 1
    call = result.tool_trajectory[0]
    assert call.name == "echo"
    assert call.args == {"text": "hello"}
    assert call.result == "tool:hello"
    assert call.ok is True


async def test_trajectory_dicts_serializable() -> None:
    agent = _agent()
    result = await agent.run("hi")
    dicts = result.trajectory_dicts()
    assert dicts[0]["tool"] == "echo"
    assert dicts[0]["result"] == "tool:hi"


async def test_unknown_tool_recorded_and_raised() -> None:
    agent = _agent()

    class Boom(BaseAgent):
        async def plan(self, user_input):  # noqa: ANN001
            self.call_tool("nope", {})
            return ""

    with pytest.raises(KeyError):
        await Boom(agent.llm, tools={}).run("x")

    # the failed call is still on the (reset) trajectory of the Boom instance
    # — we inspect via a fresh run with recording kept
    class Recorder(Boom):
        pass

    rec = Recorder(agent.llm, tools={})
    with pytest.raises(KeyError):
        await rec.run("x")
    # BaseAgent resets trajectory at run() start; after raise it holds the failure
    assert rec._trajectory[0].ok is False  # noqa: SLF001
    assert "unknown tool" in rec._trajectory[0].error  # noqa: SLF001


async def test_tool_error_recorded_not_raised() -> None:
    def bad_tool(**kwargs):  # noqa: ANN003
        raise ValueError("tool exploded")

    fake = FakeLLM(text="ok")
    agent = EchoAgent(fake, tools={"echo": bad_tool})
    result = await agent.run("x")
    assert result.tool_trajectory[0].ok is False
    assert "tool exploded" in result.tool_trajectory[0].error
    assert result.answer == "ok"  # LLM still answered


def test_langgraph_available() -> None:
    """Decision point probe: langgraph imports and a minimal graph compiles."""
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    class S(TypedDict):
        v: int

    g = StateGraph(S)
    g.add_node("n", lambda s: {"v": s["v"] + 1})
    g.add_edge(START, "n")
    g.add_edge("n", END)
    app = g.compile()
    assert app.invoke({"v": 1})["v"] == 2
