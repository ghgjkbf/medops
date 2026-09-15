"""System / agent-health routes.

- GET  /api/v1/health            liveness + MCP registry summary
- POST /api/v1/agents/inspect    manual inspection trigger
- GET  /api/v1/agents/status     LLM mode + registry + scheduler state
- GET  /api/v1/agent-metrics     per-agent counters (P7b)
- GET  /api/v1/inspection-log    inspection run archive (P7a)
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException


def register(app: FastAPI) -> None:
    @app.get("/api/v1/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "service": "medops-core",
            "mcp_servers": app.state.registry.list_status(),
        }

    @app.post("/api/v1/agents/inspect")
    async def inspect() -> dict:
        scheduler = app.state.scheduler
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

    @app.get("/api/v1/agents/status")
    async def agents_status() -> dict:
        llm = getattr(app.state, "llm", None)
        llm_mode = getattr(llm, "provider_name", "llm-chain") if llm else "unset"
        scheduler = app.state.scheduler
        return {
            "llm_mode": llm_mode,
            "registry": app.state.registry.list_status(),
            "scheduler_running": bool(scheduler and scheduler.running),
        }

    @app.get("/api/v1/agent-metrics")
    async def agent_metrics() -> dict:
        from medops_core.agent_state import get_metrics

        items = await get_metrics(app.state.db_factory)
        return {"ok": True, "data": {"count": len(items), "items": items}}

    @app.get("/api/v1/inspection-log")
    async def inspection_log(page: int = 1, page_size: int = 20) -> dict:
        from sqlalchemy import desc, select

        from medops_core.models import InspectionLog

        factory = app.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(
                    select(InspectionLog).order_by(desc(InspectionLog.created_at))
                )
            ).all()
            items = [
                {c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows
            ]
        total = len(items)
        start = (page - 1) * page_size
        return {
            "ok": True,
            "data": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": items[start:start + page_size],
            },
        }
