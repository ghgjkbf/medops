"""P2-1: LLM fallback chain tests — no real API calls (FakeLLM / local httpx mock)."""

from __future__ import annotations

import httpx
import pytest
from medops_core.agents import (
    FakeLLM,
    LLMClient,
    LLMUnavailableError,
    Message,
    ProviderConfig,
    env_providers,
    rule_based_fallback,
)


def _provider(name: str, port: int) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key=name,  # identity for the mock transport
        model="test-model",
        timeout_s=2.0,
    )


def _openai_mock_handler(calls: list[str], fail_names: set[str]):
    """httpx mock transport: OpenAI-compatible /chat/completions responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        # provider identity via api-key header we set per provider
        key = request.headers.get("authorization", "")
        name = key.removeprefix("Bearer ")
        calls.append(name)
        if name in fail_names:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": f"answer-from-{name}"}}
                ],
            },
        )

    return handler


@pytest.fixture()
def patch_transport(monkeypatch: pytest.MonkeyPatch):
    """Route LLMClient's httpx through a mock transport keyed by provider name."""
    calls: list[str] = []

    def _install(fail_names: set[str]):
        transport = httpx.MockTransport(_openai_mock_handler(calls, fail_names))

        real_init = LLMClient.__init__

        def patched_init(self, providers=None):  # noqa: ANN001
            real_init(self, providers)
            self.providers = providers or self.providers

        import medops_core.agents.llm as llm_mod

        original_async_openai = llm_mod.AsyncOpenAI

        def factory(**kwargs):  # noqa: ANN003
            kwargs["http_client"] = httpx.AsyncClient(transport=transport)
            return original_async_openai(**kwargs)

        monkeypatch.setattr(llm_mod, "AsyncOpenAI", factory)
        return calls

    return _install


async def test_single_provider_success(patch_transport) -> None:
    calls = patch_transport(set())
    client = LLMClient(providers=[_provider("p1", 8001)])
    result = await client.chat([Message(role="user", content="hi")])
    assert result.text == "answer-from-p1"
    assert result.provider_used == "p1"
    assert calls == ["p1"]


async def test_first_fails_second_wins(patch_transport) -> None:
    calls = patch_transport({"p1"})
    client = LLMClient(providers=[_provider("p1", 8001), _provider("p2", 8002)])
    result = await client.chat([Message(role="user", content="hi")])
    assert result.provider_used == "p2"
    assert result.text == "answer-from-p2"
    assert calls == ["p1", "p2"]  # both were tried, in order


async def test_all_fail_raises(patch_transport) -> None:
    patch_transport({"p1", "p2"})
    client = LLMClient(providers=[_provider("p1", 8001), _provider("p2", 8002)])
    with pytest.raises(LLMUnavailableError) as exc:
        await client.chat([Message(role="user", content="hi")])
    assert "2 providers failed" in str(exc.value)


async def test_no_providers_raises() -> None:
    client = LLMClient(providers=[])
    with pytest.raises(LLMUnavailableError):
        await client.chat([Message(role="user", content="hi")])


async def test_latency_recorded(patch_transport) -> None:
    patch_transport(set())
    client = LLMClient(providers=[_provider("p1", 8001)])
    result = await client.chat([Message(role="user", content="hi")])
    assert result.latency_ms >= 0


def test_fakellm_deterministic() -> None:
    fake = FakeLLM(text="canned")
    import asyncio

    result = asyncio.run(fake.chat([Message(role="user", content="q")]))
    assert result.text == "canned"
    assert result.provider_used == "fake"
    assert len(fake.calls) == 1


def test_fakellm_scripted_failure() -> None:
    fake = FakeLLM(provider_name="fake", fail_providers={"fake"})
    import asyncio

    with pytest.raises(LLMUnavailableError):
        asyncio.run(fake.chat([Message(role="user", content="q")]))


def test_env_providers_respect_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    chain = env_providers()
    assert [p.name for p in chain] == ["ollama"]  # only local fallback without keys

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    monkeypatch.setenv("QWEN_API_KEY", "sk-y")
    chain2 = env_providers()
    assert [p.name for p in chain2] == ["deepseek", "qwen", "ollama"]


def test_rule_based_fallback_with_metrics() -> None:
    out = rule_based_fallback(
        "diagnose", context={"device_id": "ct-sim-01", "metrics": {"tube_temp": 66.6}}
    )
    assert "[规则降级]" in out
    assert "ct-sim-01" in out and "tube_temp=66.6" in out


def test_rule_based_fallback_plain() -> None:
    out = rule_based_fallback("anything")
    assert "[规则降级]" in out
