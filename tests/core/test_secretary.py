"""P2-5: secretary agent tests (FakeLLM + in-process mini registry)."""

from __future__ import annotations

import pytest
from mcp.client import Client
from mcp.server import MCPServer
from medops_core.agents.llm import FakeLLM
from medops_core.agents.secretary import SecretaryAgent, classify_intent
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig
from medops_core.models import Base, ChatMessage, ChatSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


def _mini_ct_server() -> MCPServer:
    mcp = MCPServer("mini-ct")

    @mcp.tool()
    def get_tube_stats() -> dict:
        return {
            "available": True,
            "device_id": "ct-sim-01",
            "metrics": {"tube_temp": 36.5},
            "status": "ok",
            "simulated": True,
        }

    @mcp.tool()
    def health_check() -> dict:
        return {"status": "ok"}

    return mcp


@pytest.fixture()
async def registry() -> MCPRegistry:
    reg = MCPRegistry(client_factory=lambda cfg: Client(_mini_ct_server()))
    reg.register(MCPServerConfig(name="ct", url="in-process://ct"))
    await reg.connect_all()
    yield reg


# ------------------------------------------------------------- intent rules
def test_intent_ct_temperature() -> None:
    intent = classify_intent("3号CT现在温度多少")
    assert intent.name == "status_query"
    assert intent.tool == "get_tube_stats"
    assert intent.server == "ct"


def test_intent_ventilator() -> None:
    intent = classify_intent("呼吸机氧浓度正常吗")
    assert intent.tool == "get_realtime_params"
    assert intent.server == "ventilator"


def test_intent_chitchat() -> None:
    intent = classify_intent("你好，你能做什么")
    assert intent.name == "chitchat"
    assert intent.tool is None


def test_intent_unknown() -> None:
    intent = classify_intent("今天天气怎么样")
    assert intent.name == "unknown"
    assert intent.tool is None


# ------------------------------------------------------------------- agent
async def test_secretary_calls_tool_and_answers(registry: MCPRegistry) -> None:
    fake = FakeLLM(text="当前球管温度 36.5 度，正常")
    agent = SecretaryAgent(fake, registry)
    result = await agent.run("3号CT现在温度多少")
    assert result.answer == "当前球管温度 36.5 度，正常"
    assert len(result.tool_trajectory) == 1
    call = result.tool_trajectory[0]
    assert call.name == "get_tube_stats"
    assert call.result["metrics"]["tube_temp"] == 36.5


async def test_secretary_unknown_question_no_tool(registry: MCPRegistry) -> None:
    fake = FakeLLM(text="这个问题我无法通过设备数据回答")
    agent = SecretaryAgent(fake, registry)
    result = await agent.run("今天天气怎么样")
    assert result.tool_trajectory == []
    assert result.answer  # LLM still answers


async def test_secretary_tool_unavailable_degrades(registry: MCPRegistry) -> None:
    # question routes to ventilator but only ct is registered:
    # the failed tool call is RECORDED (auditable) and the answer still goes out
    fake = FakeLLM(text="暂时无法获取呼吸机数据")
    agent = SecretaryAgent(fake, registry)
    result = await agent.run("呼吸机氧浓度多少")
    assert len(result.tool_trajectory) == 1
    assert result.tool_trajectory[0].ok is False
    assert "ventilator" in (result.tool_trajectory[0].error or "")
    assert result.answer == "暂时无法获取呼吸机数据"


async def test_secretary_persists_chat(
    registry: MCPRegistry, db_engine: AsyncEngine
) -> None:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        fake = FakeLLM(text=" answered")
        agent = SecretaryAgent(fake, registry, session=session)
        await agent.run("3号CT现在温度多少")
    async with factory() as s:
        sessions = (await s.execute(select(ChatSession))).scalars().all()
        messages = (await s.execute(select(ChatMessage))).scalars().all()
        assert len(sessions) == 1
        assert len(messages) == 2  # user + assistant
        assistant = [m for m in messages if m.role == "assistant"][0]
        assert assistant.tool_trace is not None
        assert assistant.tool_trace[0]["tool"] == "get_tube_stats"
