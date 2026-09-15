"""Maintenance plans/records, logs, metrics, chat cleanup, report generation."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, select

from medops_core.models import (
    ChatSession,
    DeviceLog,
    DeviceMetric,
    MaintenancePlan,
    MaintenanceRecord,
)
from medops_core.routers._shared import paginate, parse_before, row_dict
from medops_core.schemas import MaintenancePlanIn, MaintenanceRecordIn


def register(app: FastAPI) -> None:
    # ------------------------------------------------- maintenance plans
    @app.get("/api/v1/maintenance-plans")
    async def list_maintenance_plans(
        page: int = 1, page_size: int = 20, device_id: str | None = None
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            rows = (await s.scalars(select(MaintenancePlan))).all()
            items = [row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            return {"ok": True, "data": paginate(items, page, page_size)}

    @app.post("/api/v1/maintenance-plans", status_code=201)
    async def create_maintenance_plan(body: MaintenancePlanIn) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = MaintenancePlan(**body.model_dump())
            s.add(row)
            await s.commit()
            return {"ok": True, "data": row_dict(row)}

    @app.delete("/api/v1/maintenance-plans/{plan_id}")
    async def delete_maintenance_plan(plan_id: int) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(
                    select(MaintenancePlan).where(MaintenancePlan.id == plan_id)
                )
            ).first()
            if row is None:
                raise HTTPException(
                    status_code=404, detail="maintenance plan not found"
                )
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": 1}}

    # ----------------------------------------------- maintenance records
    @app.get("/api/v1/maintenance-records")
    async def list_maintenance_records(
        page: int = 1, page_size: int = 20, device_id: str | None = None
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(
                    select(MaintenanceRecord).order_by(
                        MaintenanceRecord.performed_at.desc()
                    )
                )
            ).all()
            items = [row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            return {"ok": True, "data": paginate(items, page, page_size)}

    @app.post("/api/v1/maintenance-records", status_code=201)
    async def create_maintenance_record(body: MaintenanceRecordIn) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = MaintenanceRecord(**body.model_dump())
            s.add(row)
            await s.commit()
            return {"ok": True, "data": row_dict(row)}

    @app.delete("/api/v1/maintenance-records/{record_id}")
    async def delete_maintenance_record(record_id: int) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(
                    select(MaintenanceRecord).where(MaintenanceRecord.id == record_id)
                )
            ).first()
            if row is None:
                raise HTTPException(
                    status_code=404, detail="maintenance record not found"
                )
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": 1}}

    # --------------------------------------------------------- logs/metrics
    @app.get("/api/v1/logs")
    async def list_logs(
        page: int = 1,
        page_size: int = 50,
        device_id: str | None = None,
        level: str | None = None,
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(select(DeviceLog).order_by(DeviceLog.ts.desc()))
            ).all()
            items = [row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            if level:
                items = [i for i in items if i["level"] == level]
            return {"ok": True, "data": paginate(items, page, page_size)}

    @app.delete("/api/v1/logs")
    async def bulk_delete_logs(
        device_id: str | None = None, before: str | None = None
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            stmt = delete(DeviceLog)
            if device_id:
                stmt = stmt.where(DeviceLog.device_id == device_id)
            cutoff = parse_before(before)
            if cutoff is not None:
                stmt = stmt.where(DeviceLog.ts < cutoff)
            result = await s.execute(stmt)
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "data": {"deleted": deleted}}

    @app.get("/api/v1/metrics")
    async def list_metrics(
        page: int = 1,
        page_size: int = 200,
        device_id: str | None = None,
        metric_name: str | None = None,
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(select(DeviceMetric).order_by(DeviceMetric.ts.desc()))
            ).all()
            items = [row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            if metric_name:
                items = [i for i in items if i["metric_name"] == metric_name]
            return {"ok": True, "data": paginate(items, page, page_size)}

    @app.delete("/api/v1/metrics")
    async def bulk_delete_metrics(
        device_id: str | None = None, before: str | None = None
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            stmt = delete(DeviceMetric)
            if device_id:
                stmt = stmt.where(DeviceMetric.device_id == device_id)
            cutoff = parse_before(before)
            if cutoff is not None:
                stmt = stmt.where(DeviceMetric.ts < cutoff)
            result = await s.execute(stmt)
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "data": {"deleted": deleted}}

    @app.delete("/api/v1/chat-sessions")
    async def clear_chat_sessions() -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            result = await s.execute(delete(ChatSession))
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "data": {"deleted": deleted}}

    # -------------------------------------------------------------- reports
    @app.post("/api/v1/reports/generate")
    async def generate_report_endpoint(hours: int = 24) -> dict:
        from medops_core.reporting import generate_report

        if not 1 <= hours <= 24 * 30:
            raise HTTPException(status_code=422, detail="hours must be 1..720")
        llm = getattr(app.state, "llm", None)
        data = await generate_report(app.state.db_factory, hours, llm=llm)
        return {"ok": True, "data": data}
