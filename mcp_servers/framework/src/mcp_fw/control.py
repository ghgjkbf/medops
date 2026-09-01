"""MCP-side ``set_fault_scenario`` tool factory (configuration downlink).

Writes control commands to the simulator's control dir; the running
medops-sim instance polls the file each tick and applies the fault
(design §5.4). This is the write path of the MCP story — a tool with
side effects on a (simulated) device.

HIGH_RISK_WRITE per design §5.1: the P2 inspector agent must obtain
human confirmation before invoking this tool.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from medops_common.constants import ActionRisk

TOOL_RISK = ActionRisk.HIGH_RISK_WRITE.value


def make_set_fault_scenario_tool(outbox: str | Path, device_type: str):
    """Build a ``set_fault_scenario`` tool bound to one device's control dir.

    Returns (tool_fn, metadata) — servers register tool_fn and attach
    metadata to their tool listings.
    """
    control_dir = Path(outbox) / "control"

    def set_fault_scenario(
        scenario: str | None = None,
        fault: dict | None = None,
        clear: bool = False,
    ) -> dict:
        """Inject a fault into the running simulator (HIGH_RISK_WRITE).

        Provide exactly one of:
        - scenario: name of a built-in scenario (e.g. "tube_overheat")
        - fault: {"target": <metric>, "params": {"kind": "step"|"ramp", ...}}
        - clear: true to clear all active faults
        """
        command: dict = {}
        if scenario:
            command["scenario"] = scenario
        elif fault:
            command["fault"] = fault
        elif clear:
            command["clear"] = True
        else:
            return {
                "accepted": False,
                "error": "one of scenario / fault / clear is required",
                "action_risk": TOOL_RISK,
                "simulated": True,
            }

        cfile = control_dir / f"{device_type}_fault.json"
        cfile.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfile.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(command), encoding="utf-8")
        os.replace(tmp, cfile)
        return {
            "accepted": True,
            "device": device_type,
            "command": command,
            "control_file": str(cfile),
            "note": "simulator applies the command on its next tick",
            "action_risk": TOOL_RISK,
            "simulated": True,
        }

    set_fault_scenario.__name__ = "set_fault_scenario"
    return set_fault_scenario
