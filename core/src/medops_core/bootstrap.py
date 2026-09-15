"""Application bootstrap helpers (LLM chain construction).

Lives outside app.py so routers can build an LLM without importing the app
factory (which would be circular).
"""

from __future__ import annotations

import logging
import os

from medops_core.agents.llm import FakeLLM, LLMClient, env_providers
from medops_core.api_registry import llm_providers_from_db

_LOG = logging.getLogger("medops")


def build_llm() -> LLMClient | FakeLLM:
    """Env providers first, then DB-registered endpoints; FakeLLM if none."""
    if os.environ.get("MEDOPS_LLM_MODE", "").lower() == "fake":
        return FakeLLM(text="[fake] 模拟回答")
    providers = list(env_providers())
    try:  # DB-registered LLM endpoints join the fallback chain (env first)
        providers.extend(llm_providers_from_db())
    except Exception:  # noqa: BLE001 - degraded mode (no DB)
        _LOG.warning("degraded mode: startup step failed", exc_info=True)
    if not providers:
        return FakeLLM(text="[fake] 未配置 LLM provider，使用模拟回答")
    return LLMClient(providers=providers)
