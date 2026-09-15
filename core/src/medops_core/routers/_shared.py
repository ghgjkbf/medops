"""Shared helpers for API routers (pagination, row serialisation, parsing)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException


def row_dict(row: Any) -> dict:  # noqa: ANN401 - ORM row -> dict helper
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def paginate(items: list[dict], page: int, page_size: int) -> dict:
    total = len(items)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items[start:start + page_size],
    }


def parse_before(raw: str | None) -> datetime | None:
    """Parse an optional ISO-8601 timestamp; naive inputs assumed UTC. 422 on bad."""
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid before timestamp: {raw!r}"
        ) from exc
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
