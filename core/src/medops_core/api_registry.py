"""External API endpoint registry: DB-backed configs for any API (P2.5).

Outbound:
- `llm_providers_from_db()` merges api_endpoint rows (kind=llm) into the
  LLM fallback chain alongside env providers.
- `ExternalApiClient` calls any registered endpoint (bearer / custom
  header auth) — exposed to agents as the `call_external_api` tool.

Inbound:
- `make_api_key_auth` validates X-API-Key against enabled api_endpoint
  rows; when no keys are configured the API stays open (demo-friendly).
"""

from __future__ import annotations

import httpx
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from medops_core.agents.llm import ProviderConfig
from medops_core.db import get_database_url
from medops_core.models import ApiEndpoint


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "+psycopg2").replace("+aiosqlite", "")


class EndpointRegistry:
    """Async-session registry of external API endpoints.

    Uses AsyncSession (matching core); all methods are async.
    """

    def __init__(self, session_factory) -> None:  # noqa: ANN001 - async_sessionmaker
        self._session_factory = session_factory

    # ------------------------------------------------------------------ read
    async def list_endpoints(
        self, kind: str | None = None, enabled_only: bool = True
    ) -> list[dict]:
        async with self._session_factory() as s:
            stmt = select(ApiEndpoint).order_by(ApiEndpoint.name)
            if kind:
                stmt = stmt.where(ApiEndpoint.kind == kind)
            if enabled_only:
                stmt = stmt.where(ApiEndpoint.enabled.is_(True))
            rows = (await s.scalars(stmt)).all()
            return [
                {
                    "name": r.name,
                    "base_url": r.base_url,
                    "api_key": r.api_key,
                    "auth_type": r.auth_type,
                    "api_header": r.api_header,
                    "model": r.model,
                    "kind": r.kind,
                    "enabled": r.enabled,
                }
                for r in rows
            ]

    # ----------------------------------------------------------------- write
    async def upsert(
        self,
        name: str,
        base_url: str,
        api_key: str = "",
        *,
        auth_type: str = "bearer",
        api_header: str | None = None,
        model: str | None = None,
        kind: str = "generic",
        enabled: bool = True,
    ) -> int:
        async with self._session_factory() as s:
            row = (
                await s.scalars(select(ApiEndpoint).where(ApiEndpoint.name == name))
            ).first()
            if row is None:
                row = ApiEndpoint(name=name, base_url=base_url)
                s.add(row)
            row.base_url = base_url
            row.api_key = api_key
            row.auth_type = auth_type
            row.api_header = api_header
            row.model = model
            row.kind = kind
            row.enabled = enabled
            await s.commit()
            return row.id

    async def set_enabled(self, name: str, enabled: bool) -> None:
        async with self._session_factory() as s:
            row = (
                await s.scalars(select(ApiEndpoint).where(ApiEndpoint.name == name))
            ).first()
            if row is not None:
                row.enabled = enabled
                await s.commit()

    async def delete(self, name: str) -> bool:
        """Remove the endpoint row entirely (reverts inbound auth when it
        carried the last key). Returns False when unknown."""
        async with self._session_factory() as s:
            row = (
                await s.scalars(select(ApiEndpoint).where(ApiEndpoint.name == name))
            ).first()
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True


def llm_providers_from_db(url: str | None = None) -> list[ProviderConfig]:
    """Sync helper: fetch kind=llm endpoints via its own sync engine.

    Empty-key rows are included too (e.g. local Ollama-style gateways);
    LLMClient decides how to handle a missing credential.
    """
    sync = _sync_url(url or get_database_url())
    engine = create_engine(sync, pool_pre_ping=True)
    try:
        with Session(bind=engine) as s:
            rows = s.scalars(
                select(ApiEndpoint)
                .where(ApiEndpoint.kind == "llm")
                .where(ApiEndpoint.enabled.is_(True))
            ).all()
            return [
                ProviderConfig(
                    name=ep.name,
                    base_url=ep.base_url,
                    api_key=ep.api_key,
                    model=ep.model or "default",
                )
                for ep in rows
            ]
    finally:
        engine.dispose()


class ExternalApiClient:
    """Call any registered endpoint with its stored credentials."""

    def __init__(self, registry: EndpointRegistry, timeout_s: float = 15.0) -> None:
        self._registry = registry
        self._timeout_s = timeout_s

    @staticmethod
    def _headers(ep: dict) -> dict[str, str]:
        headers: dict[str, str] = {}
        if ep["auth_type"] == "bearer" and ep["api_key"]:
            headers["Authorization"] = f"Bearer {ep['api_key']}"
        elif ep["auth_type"] == "header" and ep["api_key"]:
            header_name = ep["api_header"] or "X-API-Key"
            headers[header_name] = ep["api_key"]
        return headers

    async def call(self, endpoint_name: str, method: str, path: str,
                   json_body: dict | None = None) -> dict:
        """HTTP call to a registered endpoint. Returns {status, body}."""
        endpoints = {e["name"]: e for e in await self._registry.list_endpoints()}
        ep = endpoints.get(endpoint_name)
        if ep is None:
            return {"status": 0, "error": f"endpoint {endpoint_name!r} not registered"}
        url = ep["base_url"].rstrip("/") + "/" + path.lstrip("/")
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.request(
                    method.upper(), url, headers=self._headers(ep), json=json_body
                )
            try:
                body = resp.json()
            except ValueError:
                body = resp.text[:2000]
            return {"status": resp.status_code, "body": body}
        except httpx.HTTPError as exc:
            return {"status": 0, "error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------- inbound
def make_api_key_auth(session_factory):
    """FastAPI dependency: validate X-API-Key against enabled api_endpoint rows.

    No keys configured -> API stays open (demo/dev friendly).
    """

    async def dependency(x_api_key: str | None = None) -> None:
        registry = EndpointRegistry(session_factory)
        endpoints = await registry.list_endpoints(enabled_only=True)
        keys = {e["api_key"] for e in endpoints if e["api_key"]}
        if not keys:
            return  # open mode
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="X-API-Key header required")
        if x_api_key not in keys:
            raise HTTPException(status_code=403, detail="invalid API key")

    return dependency
