"""test-king 式实战验证：知识优先回答（无 LLM + 无设备场景）。

用户场景：知识库里有答案，但没接设备 / LLM 不可用时报错。
期望：这类问题直接用知识库回答，不触发 LLM。
"""

import asyncio
import sys

sys.path.insert(0, "core/src")

from medops_core.agents.llm import LLMUnavailableError  # noqa: E402
from medops_core.agents.secretary import SecretaryAgent  # noqa: E402

EMBEDDED_KB = [
    {
        "id": 1,
        "title": "CT 球管过热",
        "content": (
            "原因：连续扫描导致球管散热不足。\n"
            "处理方法：1) 暂停扫描 15 分钟 2) 检查冷却风扇 3) 降低 mAs"
        ),
        "meta": {},
        "score": 9.0,
    }
]


class DeadLLM:
    """LLM 完全不可用（没配 key / 服务挂掉）—— 任何调用都抛错。"""

    provider_name = "dead"

    async def chat(self, messages):  # noqa: ANN001
        raise LLMUnavailableError("all providers down")


class FakeRegistry:
    handles: dict = {}

    def get(self, name):  # noqa: ANN001
        raise KeyError(name)


class FakeDB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def main() -> int:
    agent = SecretaryAgent(DeadLLM(), FakeRegistry(), session=None, db_factory=FakeDB)
    # 桩：知识库检索有命中
    agent.tools["search_knowledge"] = lambda query: EMBEDDED_KB

    ans = await agent.run("球管过热怎么处理")
    print("=== ANSWER ===")
    print(ans.answer)
    print("=== CHECKS ===")
    ok1 = "球管" in ans.answer and "散热" in ans.answer
    ok2 = "不可用" not in ans.answer and "providers down" not in ans.answer
    print(f"1. 知识库内容出现在回答中: {ok1}")
    print(f"2. 未抛 LLM 不可用错误: {ok2}")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
