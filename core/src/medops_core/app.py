"""medops-core FastAPI application (P2-7: agents + alerting wired in).

Startup (lifespan):
1. build LLM (env providers or fake mode),
2. load registered MCP servers from the mcp_server table (P1-10 sync),
3. connect them (degraded servers stay UNAVAILABLE without blocking),
4. build the inspector + scheduler and start it
   (interval from MEDOPS_INSPECT_SECONDS).

Endpoints:
- GET  /api/v1/health
- POST /api/v1/agents/inspect   manual inspection trigger
- POST /api/v1/chat             secretary Q&A (trajectory included)
- GET  /api/v1/agents/status

The app runs fine without PostgreSQL or MCP servers (degraded mode):
registry stays empty, scheduler is a no-op.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from medops_core.agents.inspector import InspectorAgent
from medops_core.agents.llm import FakeLLM, LLMClient, env_providers, rule_based_fallback
from medops_core.agents.scheduler import InspectionScheduler
from medops_core.agents.secretary import SecretaryAgent
from medops_core.alerting import AlertingEngine
from medops_core.db import async_session_factory
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig
from medops_core.mcp_client.sync import RegistrySync


def build_llm() -> LLMClient | FakeLLM:
    if os.environ.get("MEDOPS_LLM_MODE", "").lower() == "fake":
        return FakeLLM(text="[fake] 模拟回答")
    providers = env_providers()
    if not providers:
        return FakeLLM(text="[fake] 未配置 LLM key，使用模拟回答")
    return LLMClient(providers=providers)


def create_app(inspect_seconds: int | None = None) -> FastAPI:
    inspect_s = inspect_seconds or int(os.environ.get("MEDOPS_INSPECT_SECONDS", "60"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.llm = build_llm()

        try:  # registry from DB (mcp_server table); tolerate DB absence
            sync = RegistrySync()
            rows = sync.load_all()
        except Exception:  # noqa: BLE001 - degraded mode
            rows = []
        for row in rows:
            app.state.registry.register(
                MCPServerConfig(name=row["name"], url=row["url"], timeout_s=5.0)
            )
        await app.state.registry.connect_all()

        inspector = InspectorAgent(app.state.llm, app.state.registry, async_session_factory)
        app.state.scheduler = InspectionScheduler(inspector, interval_s=inspect_s)
        app.state.scheduler.start()
        try:
            yield
        finally:
            app.state.scheduler.stop()

    application = FastAPI(title="medops-core", lifespan=lifespan)
    application.state.registry = MCPRegistry()
    application.state.alerting = AlertingEngine()
    application.state.scheduler = None  # built in lifespan
    application.state.inspect_seconds = inspect_s

    # ---------------------------------------------------------------- routes
    @application.get("/api/v1/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "service": "medops-core",
            "mcp_servers": application.state.registry.list_status(),
        }

    @application.post("/api/v1/agents/inspect")
    async def inspect() -> dict:
        scheduler: InspectionScheduler | None = application.state.scheduler
        if scheduler is None:
            raise HTTPException(status_code=503, detail="scheduler not started")
        result = await scheduler.run_now()
        return {
            "checked_servers": result.checked_servers,
            "anomalies": [
                {"device_id": a.device_id, "level": a.level, "message": a.message}
                for a in result.anomalies
            ],
            "alerts_created": result.alerts_created,
            "work_orders_created": result.work_orders_created,
            "provider_used": result.provider_used,
        }

    @application.post("/api/v1/chat")
    async def chat(req: ChatRequest) -> dict:
        llm = getattr(application.state, "llm", None) or build_llm()
        if application.state.registry.handles:
            agent = SecretaryAgent(llm, application.state.registry, session=None)
            result = await agent.run(req.message)
            return {
                "answer": result.answer,
                "trajectory": result.trajectory_dicts(),
                "provider_used": result.provider_used,
            }
        # degraded mode: no servers -> rule-based answer, no tool calls
        return {
            "answer": rule_based_fallback(req.message),
            "trajectory": [],
            "provider_used": "rules",
        }

    @application.get("/api/v1/agents/status")
    async def agents_status() -> dict:
        llm = getattr(application.state, "llm", None)
        llm_mode = getattr(llm, "provider_name", "llm-chain") if llm else "unset"
        scheduler: InspectionScheduler | None = application.state.scheduler
        return {
            "llm_mode": llm_mode,
            "registry": application.state.registry.list_status(),
            "scheduler_running": bool(scheduler and scheduler.running),
        }

    return application


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


app = create_app()
