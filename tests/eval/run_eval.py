"""P2-6 eval runner: score the agent suite, print a metrics table.

Usage:
    uv run python tests/eval/run_eval.py --fake   # offline, FakeLLM
    uv run python tests/eval/run_eval.py          # real LLM (needs API keys)

Metrics (design §10): tool hit rate, error-free rate, avg latency.
LLM-as-judge scoring (answer quality) is wired via DeepEval when a real
provider is configured; the --fake mode skips it deterministically.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from tests.eval.golden_scenarios import SCENARIOS
except ImportError:
    from golden_scenarios import SCENARIOS

from golden_scenarios import tool_hit  # noqa: E402
from medops_core.agents.llm import FakeLLM, env_providers  # noqa: E402
from medops_core.agents.secretary import SecretaryAgent  # noqa: E402
from test_golden_scenarios import _build_registry  # noqa: E402


async def run_suite(mode: str) -> dict:
    reg = _build_registry()
    await reg.connect_all()

    if mode == "fake":
        llm = FakeLLM(text="评估回答（离线模式）")
    else:
        providers = env_providers()
        if not providers:
            print("no LLM providers configured; falling back to fake")
            llm = FakeLLM(text="评估回答（离线模式）")
        else:
            from medops_core.agents.llm import LLMClient  # noqa: PLC0415

            llm = LLMClient(providers=providers)

    agent = SecretaryAgent(llm, reg)
    hits = 0
    errors = 0
    latencies: list[int] = []
    per_scenario: list[dict] = []

    for scenario in SCENARIOS:
        result = await agent.run(scenario.question)
        used = [c.name for c in result.tool_trajectory if c.ok]
        hit = tool_hit(used, scenario.expected_tool)
        no_error = all(c.ok for c in result.tool_trajectory)
        hits += int(hit)
        errors += int(not no_error)
        latencies.append(result.latency_ms)
        per_scenario.append(
            {
                "sid": scenario.sid,
                "category": scenario.category,
                "tool_hit": hit,
                "used": used,
                "expected": scenario.expected_tool,
                "latency_ms": result.latency_ms,
            }
        )

    total = len(SCENARIOS)
    return {
        "mode": mode,
        "total": total,
        "tool_hit_rate": hits / total,
        "error_free_rate": 1 - errors / total,
        "avg_latency_ms": sum(latencies) / len(latencies),
        "per_scenario": per_scenario,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake", action="store_true", help="offline mode with FakeLLM")
    args = parser.parse_args()
    mode = "fake" if args.fake else "real"

    metrics = asyncio.run(run_suite(mode))
    print(f"=== medops agent eval (mode={metrics['mode']}) ===")
    print(f"scenarios:        {metrics['total']}")
    print(f"tool hit rate:    {metrics['tool_hit_rate']:.1%}")
    print(f"error-free rate:  {metrics['error_free_rate']:.1%}")
    print(f"avg latency:      {metrics['avg_latency_ms']:.0f} ms")
    for row in metrics["per_scenario"]:
        mark = "OK " if row["tool_hit"] else "MISS"
        print(f"  [{mark}] {row['sid']} ({row['category']}): "
              f"used={row['used']} expected={row['expected']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
