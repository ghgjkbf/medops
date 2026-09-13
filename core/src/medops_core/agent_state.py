"""Agent persistence — state / metrics / inspection log tables (P7a/P7b)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from medops_core.models import AgentMetrics, AgentState, InspectionLog


async def save_state(factory, agent: str, session_id: str, data: dict[str, Any]) -> None:
    blob = json.dumps(data, ensure_ascii=False, default=str)
    async with factory() as s:
        existing = (await s.scalars(
            select(AgentState).where(AgentState.agent == agent,
                                    AgentState.session_id == session_id)
        )).first()
        if existing:
            existing.data = blob
            existing.updated_at = datetime.now(UTC)
        else:
            s.add(AgentState(agent=agent, session_id=session_id, data=blob))
        await s.commit()


async def load_state(factory, agent: str, session_id: str) -> dict[str, Any] | None:
    async with factory() as s:
        row = (await s.scalars(
            select(AgentState).where(AgentState.agent == agent,
                                    AgentState.session_id == session_id)
        )).first()
    if row is None:
        return None
    return json.loads(row.data)


async def delete_state(factory, agent: str, session_id: str) -> None:
    async with factory() as s:
        await s.execute(
            delete(AgentState).where(AgentState.agent == agent,
                                     AgentState.session_id == session_id)
        )
        await s.commit()


async def record_metrics(factory, agent: str, session_id: str,
                         counters: dict[str, int]) -> None:
    async with factory() as s:
        existing = (await s.scalars(
            select(AgentMetrics).where(AgentMetrics.agent == agent)
        )).first()
        if existing:
            for k, v in counters.items():
                setattr(existing, k, getattr(existing, k, 0) + v)
        else:
            s.add(AgentMetrics(agent=agent, **counters))
        await s.commit()


async def get_metrics(factory) -> list[dict[str, Any]]:
    async with factory() as s:
        rows = (await s.scalars(select(AgentMetrics))).all()
    return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]


async def log_inspection(factory, result: dict[str, Any]) -> None:
    async with factory() as s:
        s.add(InspectionLog(
            checked_count=len(result.get("checked_servers", [])),
            alerts_created=result.get("alerts_created", 0),
            work_orders_created=len(result.get("work_orders_created", [])),
            anomalies=json.dumps([{"device_id": a.get("device_id"),
                                   "rule": a.get("rule"),
                                   "message": a.get("message")}
                                  for a in result.get("anomalies", [])],
                                 ensure_ascii=False),
        ))
        await s.commit()
