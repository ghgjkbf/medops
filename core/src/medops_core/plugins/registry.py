"""P6c: agent plugin registry — manifest-driven builtin skills.

Each plugin declares a manifest (name/description/risk/entry) and its state
(enabled/disabled) persists in the ``plugin`` table. Risky skills
(console_actor / doc_searcher) are CONFIDENT-OFF by default and gated by
``MEDOPS_PLUGIN_*`` env flags at runtime — even when enabled in the UI they
refuse to act without the env gate (defense in depth).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update

from medops_core.models import Plugin

SkillFn = Callable[[dict[str, Any]], "Awaitable[dict[str, Any]] | dict[str, Any]"]


@dataclass
class PluginManifest:
    name: str
    description: str
    risk: str = "safe"  # safe | triggered | system
    needs_gate: str | None = None  # env var that must be set (console/search)
    entry: str = ""


_BUILTIN_MANIFESTS: dict[str, PluginManifest] = {}


def register_manifest(manifest: PluginManifest) -> None:
    _BUILTIN_MANIFESTS[manifest.name] = manifest


def manifests() -> dict[str, PluginManifest]:
    return dict(_BUILTIN_MANIFESTS)


def validate_manifest(payload: dict) -> PluginManifest:
    name = str(payload.get("name", "")).strip()
    if not name or len(name) > 64 or not name.isalnum():
        raise ValueError("plugin name must be a non-empty alphanumeric string")
    description = str(payload.get("description", ""))[:255]
    risk = str(payload.get("risk", "safe"))
    if risk not in ("safe", "triggered", "system"):
        raise ValueError(f"invalid risk {risk!r}")
    needs_gate = payload.get("needs_gate") or None
    return PluginManifest(name=name, description=description, risk=risk, needs_gate=needs_gate)


def gate_allowed(manifest: PluginManifest) -> str | None:
    """None when the gate is satisfied; otherwise an explanation string."""
    if manifest.needs_gate and not os.environ.get(manifest.needs_gate):
        return f"plugin '{manifest.name}' 需要环境变量 {manifest.needs_gate}=1 才会执行"
    return None


async def list_plugins(factory) -> list[dict[str, Any]]:
    async with factory() as s:
        rows = (await s.scalars(select(Plugin))).all()
    by_name = {r.name: r for r in rows}
    out: list[dict[str, Any]] = []
    for name, m in manifests().items():
        row = by_name.get(name)
        out.append(
            {
                "name": name,
                "description": m.description,
                "risk": m.risk,
                "needs_gate": m.needs_gate or "",
                "enabled": bool(row.enabled) if row is not None else False,
                "gate_ok": gate_allowed(m) is None,
            }
        )
    return out


async def set_plugin_state(factory, name: str, enabled: bool) -> dict[str, Any]:
    manifest = _BUILTIN_MANIFESTS.get(name)
    if manifest is None:
        raise KeyError(name)
    async with factory() as s:
        row = (await s.scalars(select(Plugin).where(Plugin.name == name))).first()
        if row is None:
            s.add(Plugin(name=name, description=manifest.description, risk=manifest.risk,
                         enabled=enabled))
        else:
            await s.execute(update(Plugin).where(Plugin.name == name).values(enabled=enabled))
        await s.commit()
    return {"name": name, "enabled": enabled}


async def is_enabled(factory, name: str) -> bool:
    async with factory() as s:
        row = (await s.scalars(select(Plugin).where(Plugin.name == name))).first()
    return bool(row.enabled) if row is not None else False


class PluginError(Exception):
    pass


class GateBlocked(PluginError):
    pass


async def run_skill(
    name: str,
    args: dict[str, Any],
    *,
    factory=None,
    llm=None,
    registry=None,
) -> dict[str, Any]:
    """Dispatch to a builtin skill with gate enforcement + optional LLM step."""
    manifest = _BUILTIN_MANIFESTS.get(name)
    if manifest is None:
        raise KeyError(name)
    # defense in depth: env gate checked on EVERY invocation
    reason = gate_allowed(manifest)
    if reason is not None:
        raise GateBlocked(reason)
    if factory is not None and not await is_enabled(factory, name):
        raise GateBlocked(f"plugin '{name}' 未启用")
    ctx: dict[str, Any] = {
        "factory": factory,
        "llm": llm,
        "registry": registry,
        "args": args,
    }
    from medops_core.plugins import builtin  # noqa: PLC0415

    fn = getattr(builtin, f"skill_{name}", None)
    if fn is None:
        raise KeyError(f"no implementation for plugin {name!r}")
    result = await fn(ctx)
    enhance = getattr(builtin, f"enhance_{name}", None)
    if enhance is not None and llm is not None and getattr(llm, "provider_name", "") not in (
        "fake",
        "",
    ):
        try:
            enhanced = await enhance(ctx, result)
            if enhanced:
                result["llm_enhanced"] = True
                result["output"] = enhanced
        except Exception:  # noqa: BLE001 - enhancement is best effort
            pass
    return {"ok": True, "plugin": name, **result}


# ---- builtin manifests ---------------------------------------------------
register_manifest(PluginManifest("prompt_builder", "提示词生成：把需求合成结构化提示词", "safe"))
register_manifest(PluginManifest("code_guard", "代码生成与检查：修复脚本生成+编译/静态校验",
                    "safe"))
register_manifest(PluginManifest("tool_finder", "寻找工具：按关键词推荐 MCP 工具/管家操作", "safe"))
register_manifest(
    PluginManifest("console_actor", "电脑控制：设备控制台动作模拟", "system",
    needs_gate="MEDOPS_PLUGIN_CONSOLE"),
)
register_manifest(
    PluginManifest("doc_searcher", "电脑搜索：知识/文档检索（默认离线，联网可选）", "system",
    needs_gate="MEDOPS_PLUGIN_SEARCH"),
)
