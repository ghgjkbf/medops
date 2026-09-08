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
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from medops_common.constants import (
    WORK_ORDER_TRANSITIONS,
    WorkOrderStatus,
    can_transition_work_order,
)
from pydantic import BaseModel
from sqlalchemy import delete, select, update

from medops_core import knowledge
from medops_core.agents.butler import ButlerAgent
from medops_core.agents.inspector import InspectorAgent
from medops_core.agents.llm import FakeLLM, LLMClient, env_providers, rule_based_fallback
from medops_core.agents.scheduler import InspectionScheduler
from medops_core.agents.secretary import SecretaryAgent
from medops_core.alerting import AlertingEngine
from medops_core.api_registry import (
    EndpointRegistry,
    ExternalApiClient,
    llm_providers_from_db,
)
from medops_core.db import async_session_factory
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig
from medops_core.mcp_client.sync import RegistrySync
from medops_core.models import (
    Alert,
    ButlerAudit,
    ChatMessage,
    ChatSession,
    Device,
    DeviceLog,
    DeviceMetric,
    MaintenancePlan,
    MaintenanceRecord,
    McpServer,
    WorkOrder,
)
from medops_core.schemas import (
    ButlerTaskIn,
    DeviceIn,
    KnowledgeIn,
    MaintenancePlanIn,
    MaintenanceRecordIn,
    McpServerIn,
    WorkOrderIn,
    WorkOrderPatch,
)
from medops_core.ws import ConnectionManager, WebSocketSink


def build_llm() -> LLMClient | FakeLLM:
    if os.environ.get("MEDOPS_LLM_MODE", "").lower() == "fake":
        return FakeLLM(text="[fake] 模拟回答")
    providers = list(env_providers())
    try:  # DB-registered LLM endpoints join the fallback chain (env first)
        providers.extend(llm_providers_from_db())
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

        # builtin knowledge seed (idempotent; KB optional — never block startup)
        try:
            seeded = await knowledge.seed_builtin(async_session_factory)
            if seeded:
                logging.getLogger("medops").info(
                    "knowledge: seeded %d builtin docs", seeded
                )
        except Exception:  # noqa: BLE001 - degraded mode
            logging.getLogger("medops").warning("knowledge seed skipped", exc_info=True)

        # alert push chain: inspector -> notifier -> WebSocketSink broadcast
        ws_sink = WebSocketSink(app.state.ws_manager)

        def _alert_notifier(alerts: list) -> None:  # noqa: ANN001
            for a in alerts:
                ws_sink({
                    "device_id": a.device_id,
                    "level": a.level,
                    "kind": getattr(a, "kind", "fault"),
                    "message": a.message,
                    "attribution": a.attribution,
                    "work_order_id": a.work_order_id,
                })

        inspector = InspectorAgent(
            app.state.llm, app.state.registry, async_session_factory,
            notifier=_alert_notifier,
        )
        app.state.scheduler = InspectionScheduler(inspector, interval_s=inspect_s)
        app.state.scheduler.start()
        # butler (P5a): the inspector doubles as the management agent
        butler = ButlerAgent(
            db_factory=async_session_factory,
            registry=app.state.registry,
            scheduler=app.state.scheduler,
        )
        app.state.butler = butler
        inspector.butler = butler
        status_task = asyncio.create_task(_status_broadcaster(app))
        try:
            yield
        finally:
            status_task.cancel()
            app.state.scheduler.stop()

    async def _status_broadcaster(app: FastAPI) -> None:
        """Push an MCP status summary to dashboard clients every 10s."""
        import logging  # noqa: PLC0415

        log = logging.getLogger("medops.ws")
        while True:
            await asyncio.sleep(10)
            try:
                if app.state.ws_manager.count:
                    await app.state.ws_manager.broadcast({
                        "type": "status",
                        "registry": app.state.registry.list_status(),
                    })
            except Exception:  # noqa: BLE001 - one bad cycle must not kill the task
                log.exception("status broadcast cycle failed")

    application = FastAPI(title="medops-core", lifespan=lifespan)
    application.state.registry = MCPRegistry()
    application.state.alerting = AlertingEngine()
    application.state.ws_manager = ConnectionManager()
    application.state.scheduler = None  # built in lifespan
    application.state.inspect_seconds = inspect_s
    application.state.db_factory = async_session_factory  # overridable in tests
    application.state.endpoint_registry = EndpointRegistry(async_session_factory)
    application.state.external_api = ExternalApiClient(application.state.endpoint_registry)

    # Inbound API-key auth: enforced only when at least one key is registered
    # (demo/dev friendly open mode otherwise). Health stays always open.
    @application.middleware("http")
    async def inbound_auth_middleware(request, call_next):  # noqa: ANN001
        path = request.url.path
        if path.startswith("/api/v1") and path != "/api/v1/health":
            try:
                endpoints = await application.state.endpoint_registry.list_endpoints(
                    enabled_only=True
                )
                keys = {e["api_key"] for e in endpoints if e["api_key"]}
            except Exception:  # noqa: BLE001 - DB absent -> open mode
                keys = set()
            if keys:
                provided = request.headers.get("X-API-Key")
                if not provided:
                    return JSONResponse(
                        status_code=401, content={"detail": "X-API-Key header required"}
                    )
                if provided not in keys:
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
            agent = SecretaryAgent(
                llm, application.state.registry, session=None,
                db_factory=application.state.db_factory,
                butler=application.state.butler,
            )
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
        eps = await application.state.endpoint_registry.list_endpoints(enabled_only=False)
        # never echo credentials back
        for e in eps:
            e["api_key"] = "***" if e["api_key"] else ""
        return {"count": len(eps), "endpoints": eps}

    @application.put("/api/v1/endpoints")
    async def upsert_endpoint(ep: EndpointIn) -> dict:  # noqa: ANN401 - pydantic body
        if ep.auth_type not in ("bearer", "header", "none"):
            raise HTTPException(status_code=422, detail="auth_type must be bearer|header|none")
        endpoint_id = await application.state.endpoint_registry.upsert(
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
    async def delete_endpoint(name: str) -> dict:
        deleted = await application.state.endpoint_registry.delete(name)
        if not deleted:
            raise HTTPException(status_code=404, detail="endpoint not found")
        return {"ok": True, "data": {"deleted": name}}

    @application.patch("/api/v1/endpoints/{name}")
    async def patch_endpoint(name: str, body: EndpointPatch) -> dict:
        await application.state.endpoint_registry.set_enabled(name, body.enabled)
        eps = await application.state.endpoint_registry.list_endpoints(enabled_only=False)
        match = [e for e in eps if e["name"] == name]
        if not match:
            raise HTTPException(status_code=404, detail="endpoint not found")
        match[0]["api_key"] = "***" if match[0]["api_key"] else ""
        return {"ok": True, "data": match[0]}

    @application.post("/api/v1/endpoints/call")
    async def call_endpoint(req: EndpointCallIn) -> dict:
        client: ExternalApiClient = application.state.external_api
        return await client.call(req.endpoint, req.method, req.path, req.json_body)

    # ----------------------------------------------- MCP quick-config (P4b)
    @application.get("/api/v1/mcp-servers")
    async def list_mcp_servers() -> dict:
        return {"ok": True, "data": {"items": application.state.registry.list_status()}}

    @application.post("/api/v1/mcp-servers", status_code=201)
    async def register_mcp_server(body: McpServerIn) -> dict:
        # live registry: register + attempt connect (degrades to unavailable)
        handle = application.state.registry.register(
            MCPServerConfig(name=body.name, url=body.url)
        )
        await handle.connect()
        # persist via the async db factory (test-swappable), not RegistrySync
        # (which reads the real DATABASE_URL and would break test isolation).
        factory = application.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(McpServer).where(McpServer.name == body.name))
            ).first()
            if row is None:
                row = McpServer(name=body.name, endpoint=body.url)
                s.add(row)
            row.transport = "streamable-http"
            row.endpoint = body.url
            row.health = handle.state.value
            row.tool_list = [{"name": t} for t in handle.tools]
            row.last_heartbeat = datetime.now(UTC)
            await s.commit()
        return {"ok": True, "data": handle.status()}

    @application.delete("/api/v1/mcp-servers/{name}")
    async def remove_mcp_server(name: str) -> dict:
        removed = await application.state.registry.remove(name)
        db_deleted = False
        factory = application.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(McpServer).where(McpServer.name == name))
            ).first()
            if row is not None:
                await s.delete(row)
                await s.commit()
                db_deleted = True
        if not removed and not db_deleted:
            raise HTTPException(status_code=404, detail="mcp server not found")
        return {"ok": True, "data": {"deleted": name}}

    # ------------------------------------------------- knowledge base (P4c)
    _KB_MAX_BYTES = 512 * 1024

    @application.get("/api/v1/knowledge")
    async def knowledge_endpoint(q: str | None = None, limit: int = 8) -> dict:
        factory = application.state.db_factory
        if q:
            items = await knowledge.search_documents(factory, q, limit=limit)
        else:
            items = await knowledge.list_documents(factory)
        return {"ok": True, "data": {"count": len(items), "items": items,
                                     "backend": knowledge.active_backend()}}

    @application.post("/api/v1/knowledge", status_code=201)
    async def knowledge_add(body: KnowledgeIn) -> dict:
        factory = application.state.db_factory
        meta: dict[str, Any] = {"source": "manual"}
        if body.device_type:
            meta["device_type"] = body.device_type
        doc_id = await knowledge.add_document(factory, body.title, body.content, meta)
        return {"ok": True, "data": {"id": doc_id, "title": body.title}}

    @application.post("/api/v1/knowledge/import", status_code=201)
    async def knowledge_import(file: UploadFile) -> dict:
        raw = await file.read()
        if len(raw) > _KB_MAX_BYTES:
            raise HTTPException(status_code=422, detail="file too large (max 512 KB)")
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            raise HTTPException(status_code=422, detail="file is empty")
        title = file.filename or "imported-doc"
        doc_id = await knowledge.add_document(
            application.state.db_factory, title, text,
            {"source": "upload", "filename": title},
        )
        return {"ok": True, "data": {"id": doc_id, "title": title, "chars": len(text)}}

    @application.delete("/api/v1/knowledge/{doc_id}")
    async def knowledge_delete(doc_id: int) -> dict:
        if not await knowledge.delete_document(application.state.db_factory, doc_id):
            raise HTTPException(status_code=404, detail="knowledge doc not found")
        return {"ok": True, "data": {"deleted": doc_id}}

    # ------------------------------------------------------- butler (P5a)
    @application.post("/api/v1/butler/task")
    async def butler_task(body: ButlerTaskIn) -> dict:
        butler = application.state.butler
        if butler is None:
            raise HTTPException(status_code=503, detail="butler unavailable")
        result = await butler.execute_task(body.task, body.confirm_token)
        return {"ok": True, "data": result}

    @application.get("/api/v1/butler/audit")
    async def butler_audit_list(limit: int = 50) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(
                    select(ButlerAudit).order_by(ButlerAudit.ts.desc()).limit(limit)
                )
            ).all()
        items = [
            {"id": r.id, "ts": r.ts.isoformat(), "tool": r.tool, "args": r.args,
             "result": r.result, "risk": r.risk, "confirmed": r.confirmed}
            for r in rows
        ]
        return {"ok": True, "data": {"count": len(items), "items": items}}

    # ------------------------------------------------- resource API (P3-1)
    @application.get("/api/v1/devices")
    async def list_devices(
        page: int = 1, page_size: int = 20,
        device_type: str | None = None, status: str | None = None,
    ) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            rows = (await s.scalars(select(Device))).all()
            items = [_row_dict(r) for r in rows]
            if device_type:
                items = [i for i in items if i["device_type"] == device_type]
            if status:
                items = [i for i in items if i["status"] == status]
            return {"ok": True, "data": _paginate(items, page, page_size)}

    @application.post("/api/v1/devices", status_code=201)
    async def create_device(body: DeviceIn) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            exists = (
                await s.scalars(
                    select(Device).where(Device.device_id == body.device_id)
                )
            ).first()
            if exists is not None:
                raise HTTPException(status_code=409, detail="device_id already exists")
            s.add(Device(**body.model_dump()))
            await s.commit()
        return {"ok": True, "data": body.model_dump()}

    @application.get("/api/v1/devices/{device_id}")
    async def get_device(device_id: str) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(
                    select(Device).where(Device.device_id == device_id)
                )
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="device not found")
            return {"ok": True, "data": _row_dict(row)}

    @application.delete("/api/v1/devices/{device_id}")
    async def delete_device(device_id: str) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(Device).where(Device.device_id == device_id))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="device not found")
            # cascade-clean rows keyed by this device_id (plain-string refs, no FK)
            await s.execute(delete(MaintenanceRecord).where(
                MaintenanceRecord.device_id == device_id))
            await s.execute(delete(Alert).where(Alert.device_id == device_id))
            await s.execute(delete(WorkOrder).where(WorkOrder.device_id == device_id))
            await s.execute(delete(MaintenancePlan).where(
                MaintenancePlan.device_id == device_id))
            await s.execute(delete(DeviceLog).where(DeviceLog.device_id == device_id))
            await s.execute(delete(DeviceMetric).where(
                DeviceMetric.device_id == device_id))
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": device_id}}

    @application.get("/api/v1/alerts")
    async def list_alerts(
        page: int = 1, page_size: int = 20,
        device_id: str | None = None, level: str | None = None,
    ) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            rows = (await s.scalars(select(Alert).order_by(Alert.created_at.desc()))).all()
            items = [_row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            if level:
                items = [i for i in items if i["level"] == level]
            return {"ok": True, "data": _paginate(items, page, page_size)}

    @application.delete("/api/v1/alerts/{alert_id}")
    async def delete_alert(alert_id: int) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = (await s.scalars(select(Alert).where(Alert.id == alert_id))).first()
            if row is None:
                raise HTTPException(status_code=404, detail="alert not found")
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": 1}}

    @application.delete("/api/v1/alerts")
    async def bulk_delete_alerts(
        device_id: str | None = None, level: str | None = None, before: str | None = None,
    ) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            stmt = delete(Alert)
            if device_id:
                stmt = stmt.where(Alert.device_id == device_id)
            if level:
                stmt = stmt.where(Alert.level == level)
            cutoff = _parse_before(before)
            if cutoff is not None:
                stmt = stmt.where(Alert.created_at < cutoff)
            result = await s.execute(stmt)
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "data": {"deleted": deleted}}

    @application.get("/api/v1/work-orders")
    async def list_work_orders(
        page: int = 1, page_size: int = 20,
        device_id: str | None = None, status: str | None = None,
    ) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            rows = (await s.scalars(select(WorkOrder).order_by(WorkOrder.created_at.desc()))).all()
            items = [_row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            if status:
                items = [i for i in items if i["status"] == status]
            return {"ok": True, "data": _paginate(items, page, page_size)}

    @application.post("/api/v1/work-orders", status_code=201)
    async def create_work_order(body: WorkOrderIn) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = WorkOrder(**body.model_dump(), status=WorkOrderStatus.PENDING.value)
            s.add(row)
            await s.commit()
            return {"ok": True, "data": _row_dict(row)}

    @application.patch("/api/v1/work-orders/{work_order_id}")
    async def patch_work_order(work_order_id: int, body: WorkOrderPatch) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(WorkOrder).where(WorkOrder.id == work_order_id))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="work order not found")
            if body.status is not None and body.status != row.status:
                if body.status not in WORK_ORDER_TRANSITIONS:
                    raise HTTPException(
                        status_code=422,
                        detail=f"unknown status {body.status!r}",
                    )
                if not can_transition_work_order(row.status, body.status):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"illegal transition {row.status!r} -> {body.status!r}"
                        ),
                    )
                row.status = body.status
            if body.title is not None:
                row.title = body.title
            if body.description is not None:
                row.description = body.description
            await s.commit()
            return {"ok": True, "data": _row_dict(row)}

    @application.delete("/api/v1/work-orders/{work_order_id}")
    async def delete_work_order(work_order_id: int) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(WorkOrder).where(WorkOrder.id == work_order_id))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="work order not found")
            # detach children: alert / maintenance_record.work_order_id -> NULL
            await s.execute(update(Alert).where(Alert.work_order_id == work_order_id)
                            .values(work_order_id=None))
            await s.execute(update(MaintenanceRecord)
                            .where(MaintenanceRecord.work_order_id == work_order_id)
                            .values(work_order_id=None))
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": 1}}

    @application.get("/api/v1/maintenance-plans")
    async def list_maintenance_plans(
        page: int = 1, page_size: int = 20, device_id: str | None = None,
    ) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            rows = (await s.scalars(select(MaintenancePlan))).all()
            items = [_row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            return {"ok": True, "data": _paginate(items, page, page_size)}

    @application.post("/api/v1/maintenance-plans", status_code=201)
    async def create_maintenance_plan(body: MaintenancePlanIn) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = MaintenancePlan(**body.model_dump())
            s.add(row)
            await s.commit()
            return {"ok": True, "data": _row_dict(row)}

    @application.delete("/api/v1/maintenance-plans/{plan_id}")
    async def delete_maintenance_plan(plan_id: int) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(MaintenancePlan).where(MaintenancePlan.id == plan_id))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="maintenance plan not found")
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": 1}}

    @application.get("/api/v1/maintenance-records")
    async def list_maintenance_records(
        page: int = 1, page_size: int = 20, device_id: str | None = None,
    ) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(
                    select(MaintenanceRecord).order_by(MaintenanceRecord.performed_at.desc())
                )
            ).all()
            items = [_row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            return {"ok": True, "data": _paginate(items, page, page_size)}

    @application.post("/api/v1/maintenance-records", status_code=201)
    async def create_maintenance_record(body: MaintenanceRecordIn) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = MaintenanceRecord(**body.model_dump())
            s.add(row)
            await s.commit()
            return {"ok": True, "data": _row_dict(row)}

    @application.delete("/api/v1/maintenance-records/{record_id}")
    async def delete_maintenance_record(record_id: int) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(
                    select(MaintenanceRecord).where(MaintenanceRecord.id == record_id)
                )
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="maintenance record not found")
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": 1}}

    @application.get("/api/v1/logs")
    async def list_logs(
        page: int = 1, page_size: int = 50,
        device_id: str | None = None, level: str | None = None,
    ) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(select(DeviceLog).order_by(DeviceLog.ts.desc()))
            ).all()
            items = [_row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            if level:
                items = [i for i in items if i["level"] == level]
            return {"ok": True, "data": _paginate(items, page, page_size)}

    @application.delete("/api/v1/logs")
    async def bulk_delete_logs(device_id: str | None = None, before: str | None = None) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            stmt = delete(DeviceLog)
            if device_id:
                stmt = stmt.where(DeviceLog.device_id == device_id)
            cutoff = _parse_before(before)
            if cutoff is not None:
                stmt = stmt.where(DeviceLog.ts < cutoff)
            result = await s.execute(stmt)
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "data": {"deleted": deleted}}

    @application.get("/api/v1/metrics")
    async def list_metrics(
        page: int = 1, page_size: int = 200,
        device_id: str | None = None, metric_name: str | None = None,
    ) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(select(DeviceMetric).order_by(DeviceMetric.ts.desc()))
            ).all()
            items = [_row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            if metric_name:
                items = [i for i in items if i["metric_name"] == metric_name]
            return {"ok": True, "data": _paginate(items, page, page_size)}

    @application.delete("/api/v1/metrics")
    async def bulk_delete_metrics(
        device_id: str | None = None, before: str | None = None,
    ) -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            stmt = delete(DeviceMetric)
            if device_id:
                stmt = stmt.where(DeviceMetric.device_id == device_id)
            cutoff = _parse_before(before)
            if cutoff is not None:
                stmt = stmt.where(DeviceMetric.ts < cutoff)
            result = await s.execute(stmt)
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "data": {"deleted": deleted}}

    @application.delete("/api/v1/chat-sessions")
    async def clear_chat_sessions() -> dict:
        factory = application.state.db_factory
        async with factory() as s:
            result = await s.execute(delete(ChatSession))
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "data": {"deleted": deleted}}

    @application.post("/api/v1/reports/generate")
    async def generate_report_endpoint(hours: int = 24) -> dict:
        from medops_core.reporting import generate_report  # noqa: PLC0415

        if not 1 <= hours <= 24 * 30:
            raise HTTPException(status_code=422, detail="hours must be 1..720")
        llm = getattr(application.state, "llm", None)
        data = await generate_report(application.state.db_factory, hours, llm=llm)
        return {"ok": True, "data": data}

    # ------------------------------------------------------ WebSocket (P3-3)
    @application.websocket("/ws/dashboard")
    async def ws_dashboard(ws: WebSocket) -> None:
        manager: ConnectionManager = application.state.ws_manager
        await manager.connect(ws)
        try:
            # immediate status snapshot on join, then keep the socket warm
            await ws.send_text(json.dumps({
                "type": "status",
                "registry": application.state.registry.list_status(),
            }, ensure_ascii=False, default=str))
            while True:
                # client -> server messages are ignored (heartbeat only)
                await ws.receive_text()
        except WebSocketDisconnect:
            await manager.disconnect(ws)

    @application.websocket("/ws/chat/{session_id}")
    async def ws_chat(ws: WebSocket, session_id: str) -> None:
        await ws.accept()
        try:
            data = json.loads(await ws.receive_text())
            message = str(data.get("message", "")).strip()
            if not message:
                await ws.send_text(json.dumps({"type": "error", "detail": "empty message"}))
                await ws.close()
                return

            async def _send(payload: dict) -> None:
                await ws.send_text(json.dumps(payload, ensure_ascii=False, default=str))

            llm = getattr(application.state, "llm", None) or build_llm()
            trajectory: list[dict[str, Any]] = []
            answer: str
            provider = "rules"
            if application.state.registry.handles:
                agent = SecretaryAgent(
                    llm, application.state.registry, session=None,
                    db_factory=application.state.db_factory,
                    butler=application.state.butler,
                )
                result = await agent.run(message)
                answer = result.answer
                trajectory = result.trajectory_dicts()
                provider = result.provider_used
            else:
                answer = rule_based_fallback(message)
                provider = "rules"
            for step in trajectory:  # event-style: trace first, answer last
                await _send({"type": "tool_trace", "step": step})
            await _send({"type": "answer", "answer": answer, "provider_used": provider})

            # persist (best effort)
            try:
                factory = application.state.db_factory
                async with factory() as s:
                    chat = ChatSession(session_id=session_id)
                    s.add(chat)
                    s.add(ChatMessage(
                        session_id=session_id, role="user", content=message,
                    ))
                    s.add(ChatMessage(
                        session_id=session_id, role="assistant", content=answer,
                        tool_trace=trajectory,
                    ))
                    await s.commit()
            except Exception:  # noqa: BLE001 - persistence is best effort
                pass
            await ws.close()
        except WebSocketDisconnect:
            pass

    # ------------------------------------------------- static frontend (P3-5)
    # Production single-process mode: serve web/dist if it has been built.
    # API routes are registered above so they take precedence. Client-side
    # routes (/devices, /alerts, ...) need an explicit SPA fallback because
    # StaticFiles(html=True) only covers the index itself.
    _dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if _dist.is_dir():

        @application.get("/{spa_path:path}", include_in_schema=False)
        async def spa_fallback(spa_path: str) -> FileResponse:
            full = _dist / spa_path
            if spa_path and full.is_file():
                return FileResponse(full)
            return FileResponse(_dist / "index.html")

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


class EndpointPatch(BaseModel):
    enabled: bool


def _row_dict(row: Any) -> dict:  # noqa: ANN401 - ORM row -> dict helper
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _paginate(items: list[dict], page: int, page_size: int) -> dict:
    total = len(items)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size,
            "items": items[start:start + page_size]}


def _parse_before(raw: str | None) -> datetime | None:
    """Parse an optional ISO-8601 timestamp; naive inputs assumed UTC. 422 on bad."""
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid before timestamp: {raw!r}") from exc
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


app = create_app()
