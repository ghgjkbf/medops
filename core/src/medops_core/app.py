"""medops-core FastAPI application factory.

Startup (lifespan):
1. build LLM (env providers or fake mode),
2. load registered MCP servers from the mcp_server table (P1-10 sync),
3. connect them (degraded servers stay UNAVAILABLE without blocking),
4. build the inspector + scheduler + butler + remediation and start them
   (interval from MEDOPS_INSPECT_SECONDS).

Routes live in ``medops_core.routers`` (domain-split); this module only wires
state, middleware, the lifespan and the SPA fallback.

The app runs fine without PostgreSQL or MCP servers (degraded mode):
registry stays empty, scheduler is a no-op.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from medops_core import knowledge, sources
from medops_core.agents.butler import ButlerAgent
from medops_core.agents.inspector import InspectorAgent
from medops_core.agents.scheduler import InspectionScheduler
from medops_core.api_registry import EndpointRegistry, ExternalApiClient
from medops_core.bootstrap import build_llm
from medops_core.db import async_session_factory
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig
from medops_core.mcp_client.sync import RegistrySync
from medops_core.remediation import RemediationService
from medops_core.routers import register_all
from medops_core.ws import ConnectionManager, WebSocketSink

__all__ = ["create_app", "app", "build_llm"]

_LOG = logging.getLogger("medops")

_PLUGINS_DIR = Path(__file__).resolve().parents[4] / "plugins"


async def _seed_plugins(factory) -> None:
    """Import every plugins/*.json manifest (idempotent, never blocks startup)."""
    if not _PLUGINS_DIR.is_dir():
        return
    from medops_core.plugins.imports import import_plugin

    for path in sorted(_PLUGINS_DIR.iterdir()):
        if path.suffix != ".json":
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            await import_plugin(
                factory,
                manifest.get("name", path.stem),
                manifest["kind"],
                description=manifest.get("description", ""),
                risk=manifest.get("risk", "safe"),
                config=manifest.get("config", {}),
            )
        except Exception:  # noqa: BLE001 - one bad manifest must not stop the rest
            _LOG.warning("plugin manifest %s skipped", path.name, exc_info=True)


async def _source_syncer(app: FastAPI) -> None:
    """P5c: periodically sync knowledge sources whose schedule is due."""
    while True:
        await asyncio.sleep(60)
        try:
            results = await sources.sync_due(app.state.db_factory)
            for r in results:
                _LOG.info(
                    "knowledge source %s synced: %s", r.get("name"), r.get("added")
                )
        except Exception:  # noqa: BLE001 - scheduled sync must never crash the app
            _LOG.warning("knowledge source sync failed", exc_info=True)


async def _status_broadcaster(app: FastAPI) -> None:
    """Push an MCP status summary to dashboard clients every 10s."""
    while True:
        await asyncio.sleep(10)
        try:
            if app.state.ws_manager.count:
                await app.state.ws_manager.broadcast(
                    {"type": "status", "registry": app.state.registry.list_status()}
                )
        except Exception:  # noqa: BLE001 - one bad cycle must not kill the task
            _LOG.exception("status broadcast cycle failed")


def create_app(inspect_seconds: int | None = None) -> FastAPI:
    inspect_s = inspect_seconds or int(os.environ.get("MEDOPS_INSPECT_SECONDS", "60"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.llm = build_llm()

        try:  # registry from DB (mcp_server table); tolerate DB absence
            rows = RegistrySync().load_all()
        except Exception:  # noqa: BLE001 - degraded mode
            rows = []
        for row in rows:
            app.state.registry.register(
                MCPServerConfig(name=row["name"], url=row["url"], timeout_s=5.0)
            )
        await app.state.registry.connect_all()

        try:  # builtin knowledge seed (idempotent; KB optional)
            seeded = await knowledge.seed_builtin(async_session_factory)
            if seeded:
                _LOG.info("knowledge: seeded %d builtin docs", seeded)
        except Exception:  # noqa: BLE001 - degraded mode
            _LOG.warning("knowledge seed skipped", exc_info=True)

        # alert push chain: inspector -> notifier -> WebSocketSink broadcast
        ws_sink = WebSocketSink(app.state.ws_manager)

        def _alert_notifier(alerts: list) -> None:  # noqa: ANN001
            for a in alerts:
                ws_sink(
                    {
                        "device_id": a.device_id,
                        "level": a.level,
                        "kind": getattr(a, "kind", "fault"),
                        "message": a.message,
                        "attribution": a.attribution,
                        "work_order_id": a.work_order_id,
                        "meta": a.meta or {},
                    }
                )

        inspector = InspectorAgent(
            app.state.llm,
            app.state.registry,
            async_session_factory,
            notifier=_alert_notifier,
        )
        app.state.scheduler = InspectionScheduler(inspector, interval_s=inspect_s)
        app.state.scheduler.start()
        app.state.inspector = inspector

        # butler (P5a): the inspector doubles as the management agent
        butler = ButlerAgent(
            db_factory=async_session_factory,
            registry=app.state.registry,
            scheduler=app.state.scheduler,
        )
        app.state.butler = butler
        inspector.butler = butler

        # remediation service (P6a): triage + repair, wired into inspection
        remediation_service = RemediationService(
            app.state.registry, butler, async_session_factory
        )
        inspector.remediation = remediation_service
        app.state.remediation = remediation_service

        await _seed_plugins(async_session_factory)

        sync_task = asyncio.create_task(_source_syncer(app))
        status_task = asyncio.create_task(_status_broadcaster(app))
        try:
            yield
        finally:
            status_task.cancel()
            sync_task.cancel()
            app.state.scheduler.stop()

    application = FastAPI(title="medops-core", lifespan=lifespan)
    application.state.registry = MCPRegistry()
    application.state.alerting = None  # wired below to avoid an import cycle
    application.state.ws_manager = ConnectionManager()
    application.state.scheduler = None  # built in lifespan
    application.state.inspect_seconds = inspect_s
    application.state.db_factory = async_session_factory  # overridable in tests
    application.state.endpoint_registry = EndpointRegistry(async_session_factory)
    application.state.external_api = ExternalApiClient(
        application.state.endpoint_registry
    )

    from medops_core.alerting import AlertingEngine

    application.state.alerting = AlertingEngine()

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
                        status_code=401,
                        content={"detail": "X-API-Key header required"},
                    )
                if provided not in keys:
                    return JSONResponse(
                        status_code=403, content={"detail": "invalid API key"}
                    )
        return await call_next(request)

    register_all(application)

    # ------------------------------------------------- static frontend (P3-5)
    # Production single-process mode: serve web/dist if it has been built.
    # API routes are registered above so they take precedence. Client-side
    # routes (/devices, /alerts, ...) need an explicit SPA fallback because
    # StaticFiles(html=True) only covers the index itself.
    _dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if _dist.is_dir():

        @application.get("/{spa_path:path}", include_in_schema=False)
        async def spa_fallback(spa_path: str) -> FileResponse:
            if spa_path.startswith("api/"):
                # unknown API paths must 404, not fall through to the SPA
                raise HTTPException(status_code=404, detail="Not Found")
            full = _dist / spa_path
            if spa_path and full.is_file():
                return FileResponse(full)
            return FileResponse(_dist / "index.html")

    return application


app = create_app()
