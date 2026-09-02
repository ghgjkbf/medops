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

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from medops_core.agents.inspector import InspectorAgent
from medops_core.agents.llm import FakeLLM, LLMClient, env_providers, rule_based_fallback
from medops_core.agents.scheduler import InspectionScheduler
from medops_core.agents.secretary import SecretaryAgent
from medops_core.alerting import AlertingEngine
from medops_core.api_registry import (
    EndpointRegistry,
    ExternalApiClient,
    llm_providers_from_db,
    make_api_key_auth,
)
from medops_core.db import async_session_factory
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig
from medops_core.mcp_client.sync import RegistrySync
from medops_core.models import ChatMessage, ChatSession


def build_llm() -> LLMClient | FakeLLM:
    if os.environ.get("MEDOPS_LLM_MODE", "").lower() == "fake":
        return FakeLLM(text="[fake] 模拟回答")
    providers = list(env_providers())
    try:  # DB-registered LLM endpoints join the fallback chain (env first)
        providers.extend(llm_providers_from_db(EndpointRegistry(async_session_factory)))
    except Exception:  # noqa: BLE001 - degraded mode (no DB)
        pass
    if not providers:
        return FakeLLM(text="[fake] 未配置 LLM provider，使用模拟回答")
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
    application.state.endpoint_registry = EndpointRegistry(async_session_factory)
    application.state.external_api = ExternalApiClient(application.state.endpoint_registry)

    # Inbound API-key auth: enforced only when at least one key is registered
    # (demo/dev friendly open mode otherwise). Health stays always open.
    api_key_auth = make_api_key_auth(async_session_factory)

    @application.middleware("http")
    async def inbound_auth_middleware(request, call_next):  # noqa: ANN001
        path = request.url.path
        if path.startswith("/api/v1") and path != "/api/v1/health":
            try:
                keys = {
                    e["api_key"]
                    for e in application.state.endpoint_registry.list_endpoints(enabled_only=True)
                    if e["api_key"]
                }
            except Exception:  # noqa: BLE001 - DB absent -> open mode
                keys = set()
            if keys:
                provided = request.headers.get("X-API-Key")
                if not provided:
                    from fastapi.responses import JSONResponse  # noqa: PLC0415

                    return JSONResponse(
                        status_code=401, content={"detail": "X-API-Key header required"}
                    )
                if provided not in keys:
                    from fastapi.responses import JSONResponse  # noqa: PLC0415

                    return JSONResponse(
                        status_code=403, content={"detail": "invalid API key"}
                    )
        return await call_next(request)

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
        trajectory: list[dict[str, Any]] = []
        answer: str
        provider = "rules"
        if application.state.registry.handles:
            agent = SecretaryAgent(llm, application.state.registry, session=None)
            result = await agent.run(req.message)
            answer = result.answer
            trajectory = result.trajectory_dicts()
            provider = result.provider_used
        else:  # degraded mode: no servers -> rule-based answer, no tool calls
            answer = rule_based_fallback(req.message)

        # persist chat (best-effort; DB may be absent in degraded mode)
        try:
            async with async_session_factory() as session:
                session.add(
                    ChatSession(session_key=f"api-{datetime.now(UTC).timestamp()}")
                )
                await session.flush()
                session.add(
                    ChatMessage(
                        session_id=1,
                        role="user",
                        content=req.message,
                    )
                )
                session.add(
                    ChatMessage(
                        session_id=1,
                        role="assistant",
                        content=answer,
                        tool_trace=trajectory,
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001 - persistence is best-effort
            pass
        return {
            "answer": answer,
            "trajectory": trajectory,
            "provider_used": provider,
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

    # ------------------------------------------------- external API onboarding
    @application.get("/api/v1/endpoints")
    async def list_endpoints() -> dict:
        eps = application.state.endpoint_registry.list_endpoints(enabled_only=False)
        # never echo credentials back
        for e in eps:
            e["api_key"] = "***" if e["api_key"] else ""
        return {"count": len(eps), "endpoints": eps}

    @application.put("/api/v1/endpoints")
    async def upsert_endpoint(ep: EndpointIn) -> dict:  # noqa: ANN401 - pydantic body
        if ep.auth_type not in ("bearer", "header", "none"):
            raise HTTPException(status_code=422, detail="auth_type must be bearer|header|none")
        endpoint_id = application.state.endpoint_registry.upsert(
            ep.name,
            ep.base_url,
            ep.api_key,
            auth_type=ep.auth_type,
            api_header=ep.api_header,
            model=ep.model,
            kind=ep.kind,
            enabled=ep.enabled,
        )
        return {"ok": True, "id": endpoint_id, "name": ep.name}

    @application.delete("/api/v1/endpoints/{name}")
    async def disable_endpoint(name: str) -> dict:
        application.state.endpoint_registry.set_enabled(name, False)
        return {"ok": True, "name": name, "enabled": False}

    @application.post("/api/v1/endpoints/call")
    async def call_endpoint(req: EndpointCallIn) -> dict:
        client: ExternalApiClient = application.state.external_api
        result = await asyncio.to_thread(
            client.call, req.endpoint, req.method, req.path, req.json_body
        )
        return result

    return application


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class EndpointIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    auth_type: str = "bearer"  # bearer | header | none
    api_header: str | None = None
    model: str | None = None
    kind: str = "generic"  # llm | generic
    enabled: bool = True


class EndpointCallIn(BaseModel):
    endpoint: str
    method: str = "GET"
    path: str = ""
    json_body: dict | None = None


app = create_app()
