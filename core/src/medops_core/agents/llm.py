"""LLM multi-provider fallback chain (design §9 layer 3).

Providers are OpenAI-compatible endpoints (DeepSeek / Qwen / Ollama all
speak the OpenAI chat-completions protocol), tried in order; a failure
moves to the next provider. When every provider fails, callers fall back
to `rule_based_fallback` so the pipeline never hard-depends on an LLM.

All Agent components accept an `llm: LLMClient` (or FakeLLM in tests).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI


class LLMUnavailableError(RuntimeError):
    """Every provider failed; caller should degrade to rule-based output."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str = ""
    model: str = ""
    timeout_s: float = 30.0


@dataclass
class LLMResult:
    text: str
    provider_used: str
    latency_ms: int


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str


def env_providers() -> list[ProviderConfig]:
    """Default chain: DeepSeek -> Qwen -> Ollama (env-driven)."""
    providers: list[ProviderConfig] = []
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
    qwen_key = os.environ.get("QWEN_API_KEY", "")
    ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

    if deepseek_key:
        providers.append(
            ProviderConfig(
                name="deepseek",
                base_url="https://api.deepseek.com/v1",
                api_key=deepseek_key,
                model="deepseek-chat",
            )
        )
    if qwen_key:
        providers.append(
            ProviderConfig(
                name="qwen",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key=qwen_key,
                model="qwen-plus",
            )
        )
    if ollama_base:  # local fallback: always last, key optional
        providers.append(
            ProviderConfig(
                name="ollama",
                base_url=ollama_base,
                api_key="ollama",
                model=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
            )
        )
    return providers


class LLMClient:
    """Try providers in order; first success wins. Raises LLMUnavailableError."""

    def __init__(self, providers: list[ProviderConfig] | None = None) -> None:
        self.providers = providers if providers is not None else env_providers()

    async def chat(self, messages: list[Message], timeout_s: float = 30.0) -> LLMResult:
        if not self.providers:
            raise LLMUnavailableError("no providers configured")
        last_error: Exception | None = None
        for provider in self.providers:
            started = time.perf_counter()
            try:
                client = AsyncOpenAI(
                    base_url=provider.base_url,
                    api_key=provider.api_key or "not-needed",
                    timeout=timeout_s,
                    max_retries=0,  # provider-level fallback handles retries
                    http_client=httpx.AsyncClient(timeout=timeout_s),
                )
                resp = await client.chat.completions.create(
                    model=provider.model,
                    messages=[{"role": m.role, "content": m.content} for m in messages],
                )
                text = (resp.choices[0].message.content) or ""
                latency_ms = int((time.perf_counter() - started) * 1000)
                return LLMResult(text=text, provider_used=provider.name, latency_ms=latency_ms)
            except Exception as exc:  # noqa: BLE001 - any failure falls through
                last_error = exc
                continue
        raise LLMUnavailableError(
            f"all {len(self.providers)} providers failed; last error: {last_error}"
        )


class FakeLLM:
    """Deterministic test double: canned text or scripted failures."""

    def __init__(
        self,
        text: str = "fake answer",
        *,
        fail_providers: set[str] | None = None,
        latency_ms: int = 1,
        provider_name: str = "fake",
    ) -> None:
        self.text = text
        self.fail_providers = fail_providers or set()
        self.latency_ms = latency_ms
        self.provider_name = provider_name
        self.calls: list[list[Message]] = []

    async def chat(self, messages: list[Message], timeout_s: float = 30.0) -> LLMResult:
        self.calls.append(messages)
        if self.provider_name in self.fail_providers:
            raise LLMUnavailableError(f"fake provider {self.provider_name} failed")
        return LLMResult(text=self.text, provider_used=self.provider_name, latency_ms=self.latency_ms)


# ------------------------------------------------------------------ rule fallback
def rule_based_fallback(prompt: str, context: dict[str, Any] | None = None) -> str:
    """Deterministic template answer when all LLM providers are down (design §9).

    Keeps the pipeline alive: alerts still get an attribution summary and
    inspections still report findings — just without natural-language gloss.
    """
    context = context or {}
    device = context.get("device_id", "")
    metrics = context.get("metrics", {})
    if metrics:
        summary = ", ".join(f"{k}={v}" for k, v in metrics.items())
        return (
            f"[规则降级] 设备 {device} 当前指标: {summary}。"
            "LLM 不可用，无法生成自然语言归因；请人工复核。"
        )
    if "create_work_order" in prompt or "工单" in prompt:
        return "[规则降级] 已按阈值规则自动创建工单，请人工确认。"
    return "[规则降级] LLM 服务不可用，返回规则引擎结论。"
