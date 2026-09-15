"""Resource routes: devices, alerts (+ remediation consent), work orders."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from medops_common.constants import (
    WORK_ORDER_TRANSITIONS,
    WorkOrderStatus,
    can_transition_work_order,
)
from sqlalchemy import delete, select, update

from medops_core.models import (
    Alert,
    Device,
    DeviceLog,
    DeviceMetric,
    MaintenancePlan,
    MaintenanceRecord,
    WorkOrder,
)
from medops_core.routers._shared import paginate, parse_before, row_dict
from medops_core.schemas import (
    DeviceIn,
    RemediationAgreeIn,
    WorkOrderIn,
    WorkOrderPatch,
)


def register(app: FastAPI) -> None:
    # ----------------------------------------------------------- devices
    @app.get("/api/v1/devices")
    async def list_devices(
        page: int = 1,
        page_size: int = 20,
        device_type: str | None = None,
        status: str | None = None,
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            rows = (await s.scalars(select(Device))).all()
            items = [row_dict(r) for r in rows]
            if device_type:
                items = [i for i in items if i["device_type"] == device_type]
            if status:
                items = [i for i in items if i["status"] == status]
            return {"ok": True, "data": paginate(items, page, page_size)}

    @app.post("/api/v1/devices", status_code=201)
    async def create_device(body: DeviceIn) -> dict:
        factory = app.state.db_factory
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

    @app.get("/api/v1/devices/{device_id}")
    async def get_device(device_id: str) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(Device).where(Device.device_id == device_id))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="device not found")
            return {"ok": True, "data": row_dict(row)}

    @app.delete("/api/v1/devices/{device_id}")
    async def delete_device(device_id: str) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(Device).where(Device.device_id == device_id))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="device not found")
            # cascade-clean rows keyed by this device_id (plain-string refs, no FK)
            await s.execute(
                delete(MaintenanceRecord).where(
                    MaintenanceRecord.device_id == device_id
                )
            )
            await s.execute(delete(Alert).where(Alert.device_id == device_id))
            await s.execute(delete(WorkOrder).where(WorkOrder.device_id == device_id))
            await s.execute(
                delete(MaintenancePlan).where(MaintenancePlan.device_id == device_id)
            )
            await s.execute(delete(DeviceLog).where(DeviceLog.device_id == device_id))
            await s.execute(
                delete(DeviceMetric).where(DeviceMetric.device_id == device_id)
            )
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": device_id}}

    # ------------------------------------------------------------ alerts
    @app.get("/api/v1/alerts")
    async def list_alerts(
        page: int = 1,
        page_size: int = 20,
        device_id: str | None = None,
        level: str | None = None,
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(select(Alert).order_by(Alert.created_at.desc()))
            ).all()
            items = [row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            if level:
                items = [i for i in items if i["level"] == level]
            return {"ok": True, "data": paginate(items, page, page_size)}

    @app.get("/api/v1/alerts/{alert_id}/remediation")
    async def alert_remediation(alert_id: int) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = (await s.scalars(select(Alert).where(Alert.id == alert_id))).first()
            if row is None:
                raise HTTPException(status_code=404, detail="alert not found")
            meta = dict(row.meta or {})
        return {"ok": True, "data": meta.get("remediation", [])}

    @app.post("/api/v1/alerts/{alert_id}/remediation/agree")
    async def alert_remediation_agree(
        alert_id: int, body: RemediationAgreeIn
    ) -> dict:
        """P6a: user consent for a device-software (or high-risk) repair."""
        service = app.state.remediation
        factory = app.state.db_factory
        async with factory() as s:
            row = (await s.scalars(select(Alert).where(Alert.id == alert_id))).first()
            if row is None:
                raise HTTPException(status_code=404, detail="alert not found")
            device_id = row.device_id
            message = row.message or ""
            meta = dict(row.meta or {})
        hit = {
            "rule": "device_system:unknown",
            "device_id": device_id,
            "message": message,
            "level": "warning",
        }
        if body.rule:
            hit["rule"] = body.rule
        outcome = await service.heal(
            hit,
            signals={"mcp_unavailable": []},
            consent="allow" if body.approve else "deny",
        )
        meta["remediation"] = meta.get("remediation") or []
        meta["remediation"].append({**outcome, "consent_from": "ui"})
        async with factory() as s:
            await s.execute(update(Alert).where(Alert.id == alert_id).values(meta=meta))
            await s.commit()
        return {"ok": True, "data": outcome}

    # NOTE: bulk delete (/alerts) must be declared BEFORE /alerts/{alert_id}
    # only if both were DELETE on the same prefix — they differ in arity, so
    # FastAPI resolves them correctly either way.
    @app.delete("/api/v1/alerts/{alert_id}")
    async def delete_alert(alert_id: int) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = (await s.scalars(select(Alert).where(Alert.id == alert_id))).first()
            if row is None:
                raise HTTPException(status_code=404, detail="alert not found")
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": 1}}

    @app.delete("/api/v1/alerts")
    async def bulk_delete_alerts(
        device_id: str | None = None,
        level: str | None = None,
        before: str | None = None,
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            stmt = delete(Alert)
            if device_id:
                stmt = stmt.where(Alert.device_id == device_id)
            if level:
                stmt = stmt.where(Alert.level == level)
            cutoff = parse_before(before)
            if cutoff is not None:
                stmt = stmt.where(Alert.created_at < cutoff)
            result = await s.execute(stmt)
            deleted = result.rowcount
            await s.commit()
        return {"ok": True, "data": {"deleted": deleted}}

    # -------------------------------------------------------- work orders
    @app.get("/api/v1/work-orders")
    async def list_work_orders(
        page: int = 1,
        page_size: int = 20,
        device_id: str | None = None,
        status: str | None = None,
    ) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            rows = (
                await s.scalars(
                    select(WorkOrder).order_by(WorkOrder.created_at.desc())
                )
            ).all()
            items = [row_dict(r) for r in rows]
            if device_id:
                items = [i for i in items if i["device_id"] == device_id]
            if status:
                items = [i for i in items if i["status"] == status]
            return {"ok": True, "data": paginate(items, page, page_size)}

    @app.post("/api/v1/work-orders", status_code=201)
    async def create_work_order(body: WorkOrderIn) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = WorkOrder(**body.model_dump(), status=WorkOrderStatus.PENDING.value)
            s.add(row)
            await s.commit()
            return {"ok": True, "data": row_dict(row)}

    @app.patch("/api/v1/work-orders/{work_order_id}")
    async def patch_work_order(work_order_id: int, body: WorkOrderPatch) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(WorkOrder).where(WorkOrder.id == work_order_id))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="work order not found")
            if body.status is not None and body.status != row.status:
                if body.status not in WORK_ORDER_TRANSITIONS:
                    raise HTTPException(
                        status_code=422, detail=f"unknown status {body.status!r}"
                    )
                if not can_transition_work_order(row.status, body.status):
                    raise HTTPException(
                        status_code=409,
                        detail=f"illegal transition {row.status!r} -> {body.status!r}",
                    )
                row.status = body.status
            if body.title is not None:
                row.title = body.title
            if body.description is not None:
                row.description = body.description
            await s.commit()
            return {"ok": True, "data": row_dict(row)}

    @app.delete("/api/v1/work-orders/{work_order_id}")
    async def delete_work_order(work_order_id: int) -> dict:
        factory = app.state.db_factory
        async with factory() as s:
            row = (
                await s.scalars(select(WorkOrder).where(WorkOrder.id == work_order_id))
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="work order not found")
            # detach children: alert / maintenance_record.work_order_id -> NULL
            await s.execute(
                update(Alert)
                .where(Alert.work_order_id == work_order_id)
                .values(work_order_id=None)
            )
            await s.execute(
                update(MaintenanceRecord)
                .where(MaintenanceRecord.work_order_id == work_order_id)
                .values(work_order_id=None)
            )
            await s.delete(row)
            await s.commit()
        return {"ok": True, "data": {"deleted": 1}}
