"""API routers — domain-split from the former monolithic app.py."""

from __future__ import annotations

from fastapi import FastAPI

from medops_core.routers import (
    agents,
    knowledge,
    maintenance,
    mcp,
    plugins,
    resources,
    system,
    websockets,
)


def register_all(app: FastAPI) -> None:
    """Mount every domain router. Order matters only for path overlap, and
    the SPA fallback is registered last by the app factory."""
    for module in (
        system,
        agents,
        mcp,
        knowledge,
        plugins,
        resources,
        maintenance,
        websockets,
    ):
        module.register(app)
