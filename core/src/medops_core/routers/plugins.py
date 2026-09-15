"""Plugin / builtin-skill routes (P6c).

Route order matters: the literal paths (/import, /import-file) MUST be
registered before /{plugin_name}, otherwise "import" is captured as a
plugin name.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, UploadFile

from medops_core.schemas import PluginImportIn, PluginRunIn, PluginStateIn


def register(app: FastAPI) -> None:
    @app.get("/api/v1/plugins")
    async def plugins_list() -> dict:
        from medops_core.plugins.registry import list_plugins

        items = await list_plugins(app.state.db_factory)
        return {"ok": True, "data": {"count": len(items), "items": items}}

    # ---- literal paths first (before /{plugin_name}) ----
    @app.post("/api/v1/plugins/import")
    async def plugins_import(body: PluginImportIn) -> dict:
        from medops_core.plugins.imports import import_plugin

        try:
            state = await import_plugin(
                app.state.db_factory,
                body.name,
                body.kind,
                description=body.description,
                risk=body.risk,
                config=body.config,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"ok": True, "data": state}

    @app.post("/api/v1/plugins/import-file")
    async def plugins_import_file(file: UploadFile) -> dict:
        """Upload a .json plugin manifest file and import it."""
        if not file.filename or not file.filename.lower().endswith(".json"):
            raise HTTPException(422, detail="only .json files accepted")
        from medops_core.plugins.imports import import_plugin

        manifest = json.loads(await file.read())
        state = await import_plugin(
            app.state.db_factory,
            name=manifest.get("name", file.filename.rsplit(".", 1)[0]),
            kind=manifest["kind"],
            description=manifest.get("description", ""),
            risk=manifest.get("risk", "safe"),
            config=manifest.get("config", {}),
        )
        return {"ok": True, "data": state}

    # ---- parameterised paths ----
    @app.post("/api/v1/plugins/{plugin_name}")
    async def plugins_set(plugin_name: str, body: PluginStateIn) -> dict:
        from medops_core.plugins.registry import set_plugin_state

        try:
            state = await set_plugin_state(
                app.state.db_factory, plugin_name, body.enabled
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="plugin not found") from None
        return {"ok": True, "data": state}

    @app.delete("/api/v1/plugins/{plugin_name}")
    async def plugins_delete(plugin_name: str) -> dict:
        from medops_core.plugins.registry import delete_plugin

        try:
            await delete_plugin(app.state.db_factory, plugin_name)
        except KeyError:
            raise HTTPException(status_code=404, detail="plugin not found") from None
        return {"ok": True, "data": {"deleted": plugin_name}}

    @app.post("/api/v1/plugins/{plugin_name}/run")
    async def plugins_run(plugin_name: str, body: PluginRunIn) -> dict:
        from medops_core.plugins.registry import GateBlocked, run_skill

        try:
            outcome = await run_skill(
                plugin_name,
                body.args,
                factory=app.state.db_factory,
                llm=app.state.llm,
                registry=app.state.registry,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="plugin not found") from None
        except GateBlocked as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"ok": True, "data": outcome}
