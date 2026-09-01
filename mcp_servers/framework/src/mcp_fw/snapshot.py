"""Shared metric-snapshot reading for device MCP servers (P1).

Simulators expose their current metrics via ``<outbox>/metrics_snapshot.json``
(atomic tmp+rename per tick, see medops_sim). MCP servers read that file;
this module centralizes the format so servers stay thin.
"""

from __future__ import annotations

import json
from pathlib import Path


def read_metrics_snapshot(outbox: str | Path) -> dict | None:
    """Read a metrics snapshot; return None if missing/corrupt (never raise)."""
    path = Path(outbox) / "metrics_snapshot.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "metrics" not in data:
        return None
    return data


def evaluate_thresholds(
    values: dict[str, float],
    rules: dict[str, dict[str, float]],
) -> dict[str, str]:
    """Evaluate simple threshold rules per metric.

    rules: metric -> {"warn_low": x, "error_low": y, "warn_high": a, "error_high": b}
    Status per metric: ok | warning | error (highest severity wins).
    """
    status: dict[str, str] = {}
    for name, value in values.items():
        rule = rules.get(name)
        if not rule:
            status[name] = "ok"
            continue
        level = "ok"
        if "error_low" in rule and value <= rule["error_low"]:
            level = "error"
        elif "warn_low" in rule and value <= rule["warn_low"]:
            level = "warning"
        if "error_high" in rule and value >= rule["error_high"]:
            level = "error"
        elif "warn_high" in rule and value >= rule["warn_high"] and level != "error":
            level = "warning"
        status[name] = level
    return status


def list_dir_files(directory: str | Path) -> dict:
    """Shared file listing used by DICOM/export checks (existence + mtimes)."""
    import os
    from datetime import UTC, datetime

    d = Path(directory)
    if not d.is_dir():
        return {"exists": False, "files": []}
    files = []
    for f in sorted(d.iterdir()):
        if f.is_file():
            st = os.stat(f)
            files.append(
                {
                    "name": f.name,
                    "size_bytes": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
                }
            )
    return {"exists": True, "files": files}
