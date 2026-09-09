"""P6a: device-system toolset shared by all device MCP servers.

The device's OWN system (firmware / config hash / onboard agent health)
lives in the simulator state, published inside the metrics snapshot and
driven by control commands (``device_system.repair``: firmware / config /
agent). Written self-contained here so servers keep depending only on
``mcp-fw`` (same pattern as ``mcp_fw.control``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp_fw.snapshot import read_metrics_snapshot

DEVICE_SYSTEM_FAULTS = ("firmware-stale", "config-drift", "agent-stuck")
DEVICE_SYSTEM_REPAIRS = ("firmware", "config", "agent")


def _write_command(outbox: str | Path, device_type: str, command: dict) -> None:
    cdir = Path(outbox) / "control"
    cdir.mkdir(parents=True, exist_ok=True)
    cfile = cdir / f"{device_type}_fault.json"
    tmp = cfile.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(command), encoding="utf-8")
    os.replace(tmp, cfile)


def get_device_info(outbox: str | Path, device_id: str) -> dict:
    """Firmware / config / onboard-agent state of the device itself."""
    snapshot = read_metrics_snapshot(outbox)
    system = (snapshot or {}).get("device_system") or {}
    return {
        "device_id": device_id,
        "device_system": system
        if system
        else {"error": "no device_system in snapshot (simulator running?)"},
        "simulated": True,
    }


def _repair(outbox: str | Path, device_type: str, repair: str) -> dict:
    _write_command(outbox, device_type, {"device_system": {"repair": repair}})
    return {"repair": repair, "applied": True, "simulated": True}


def upgrade_firmware(outbox: str | Path, device_type: str) -> dict:
    """Upgrade the device firmware to its target version."""
    return _repair(outbox, device_type, "firmware")


def apply_config(outbox: str | Path, device_type: str) -> dict:
    """Re-apply the expected device configuration."""
    return _repair(outbox, device_type, "config")


def restart_device_agent(outbox: str | Path, device_type: str) -> dict:
    """Restart the device's onboard agent (unsticks its health state)."""
    return _repair(outbox, device_type, "agent")


def register_device_system_tools(server, outbox: str | Path, device_type: str) -> None:
    """Register the four device-system tools on a device MCP server."""
    box = Path(outbox)

    def _get_device_info() -> dict:
        return get_device_info(box, f"{device_type}-sim-01")

    def _upgrade_firmware() -> dict:
        return upgrade_firmware(box, device_type)

    def _apply_config() -> dict:
        return apply_config(box, device_type)

    def _restart_device_agent() -> dict:
        return restart_device_agent(box, device_type)

    server.register_tool(_get_device_info, name="get_device_info")
    server.register_tool(_upgrade_firmware, name="upgrade_firmware")
    server.register_tool(_apply_config, name="apply_config")
    server.register_tool(_restart_device_agent, name="restart_device_agent")
