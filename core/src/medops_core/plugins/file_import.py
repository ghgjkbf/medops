"""P6c-ext: file-based plugin import — read a .json manifest from the
``plugins/`` directory, validate it, store its metadata in the DB.

File format (`.json`):
.. code-block:: json

    {
      "name": "my_scan",
      "description": "自定义扫描插件",
      "kind": "prompt_template",
      "risk": "safe",
      "config": {
        "template": "巡检 {device} 重点检查 {focus}"
      }
    }

Supported kinds: prompt_template, code_template, tool_chain, kb_query.

``plugins/`` — each .json file → one plugin (name must match filename stem).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from medops_core.plugins.imports import import_plugin

PLUGINS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "plugins"


async def scan_plugins_dir(factory) -> list[dict[str, Any]]:
    """Walk ``plugins/``, load each .json, upsert into DB.
    Returns list of import results."""
    if not PLUGINS_DIR.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for fpath in sorted(PLUGINS_DIR.iterdir()):
        if fpath.suffix.lower() != ".json":
            continue
        try:
            manifest = json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            results.append({"file": fpath.name, "error": str(exc)})
            continue
        name = manifest.get("name", "") or fpath.stem
        kind = manifest.get("kind", "")
        if not kind:
            results.append({"file": fpath.name, "error": "missing 'kind' field"})
            continue
        try:
            state = await import_plugin(
                factory,
                name=name,
                kind=kind,
                description=manifest.get("description", ""),
                risk=manifest.get("risk", "safe"),
                config=manifest.get("config", {}),
            )
            results.append({"file": fpath.name, "name": name, "status": "imported", **state})
        except ValueError as exc:
            results.append({"file": fpath.name, "error": str(exc)})
    return results


async def import_plugin_from_file(factory, filepath: str) -> dict[str, Any]:
    """Import a single plugin file (accepts absolute path or relative to plugins/)."""
    path = Path(filepath)
    if not path.is_absolute():
        path = PLUGINS_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"plugin file not found: {path}")
    if path.suffix.lower() != ".json":
        raise ValueError(f"unsupported file type {path.suffix}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    name = manifest.get("name", "") or path.stem
    kind = manifest.get("kind", "")
    if not kind:
        raise ValueError("manifest must contain 'kind'")
    return await import_plugin(
        factory,
        name=name,
        kind=kind,
        description=manifest.get("description", ""),
        risk=manifest.get("risk", "safe"),
        config=manifest.get("config", {}),
    )
