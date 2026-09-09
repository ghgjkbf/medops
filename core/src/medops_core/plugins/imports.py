"""P6c-per-request: import external plugins (declarative executor kinds only).

An imported plugin is validated metadata + a bounded executor kind:
- `prompt_template`: `config.template` fills a prompt from args (stdlib format)
- `code_template`: `config` fed into the code_guard template engine
  (`template` must be one of BUILTIN templates, `params` provided at run time)
- `tool_chain`: sequential device MCP calls (`config.steps`), system risk —
  env-gated like console_actor when risk=system
- `kb_query`: query template over the knowledge base

No arbitrary code is executed — this keeps the demo platform safe while
still allowing a team to ship new skills as JSON.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, update

from medops_core.models import Plugin

ALLOWED_KINDS = ("prompt_template", "code_template", "tool_chain", "kb_query")


async def import_plugin(factory, name: str, kind: str, description: str = "",
                        risk: str = "safe", config: dict | None = None) -> dict:
    if not name or len(name) > 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("name must be [A-Za-z0-9_-] and <= 64 chars")
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported plugin kind {kind!r} (allowed: {ALLOWED_KINDS})")
    if risk not in ("safe", "triggered", "system"):
        raise ValueError(f"invalid risk {risk!r}")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    async with factory() as s:
        row = (await s.scalars(select(Plugin).where(Plugin.name == name))).first()
        meta = {
            "kind": kind,
            "config": config,
            "imported": True,
        }
        if row is None:
            s.add(Plugin(name=name, description=description[:255], risk=risk,
                         enabled=False, meta=meta))
        else:
            await s.execute(
                update(Plugin).where(Plugin.name == name).values(
                    description=description[:255], risk=risk, meta=meta
                )
            )
        await s.commit()
    return {"name": name, "kind": kind, "risk": risk, "enabled": False}


async def imported_entry(factory, name: str) -> dict | None:
    async with factory() as s:
        row = (await s.scalars(select(Plugin).where(Plugin.name == name))).first()
    if row is None:
        return None
    meta = dict(row.meta or {})
    if not meta.get("imported"):
        return None
    return {
        "name": row.name,
        "description": row.description,
        "risk": row.risk,
        "kind": meta.get("kind"),
        "config": meta.get("config") or {},
        "enabled": bool(row.enabled),
    }


async def run_imported(ctx: dict[str, Any], entry: dict) -> dict[str, Any]:
    kind = entry["kind"]
    config = entry["config"]
    args = ctx["args"]
    if kind == "prompt_template":
        template = config.get("template", "")
        return {"output": template.format(**args) if template else str(args)}
    if kind == "code_template":
        from medops_core.plugins.builtin import skill_code_guard  # noqa: PLC0415

        result = await skill_code_guard({"args": {"template": config.get("template", ""),
                                                  "params": args.get("params", {})}, **ctx})
        return result
    if kind == "tool_chain":
        from medops_core.plugins.builtin import skill_console_actor  # noqa: PLC0415

        return await skill_console_actor({"args": {
            "device": args.get("device", config.get("device", "ct")),
            "steps": config.get("steps", []) + args.get("steps", []),
        }, **ctx})
    if kind == "kb_query":
        from medops_core.plugins.builtin import skill_doc_searcher  # noqa: PLC0415

        template = config.get("query_template", "{query}")
        return await skill_doc_searcher({"args": {
            "query": args.get("query", template.format(**args)),
        }, **ctx})
    raise ValueError(f"unsupported kind {kind!r}")
