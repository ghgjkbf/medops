"""Simulator-side fault control: watch a control file, apply injected faults.

The MCP layer writes ``control/<device>_fault.json`` (atomic tmp+rename);
the simulator polls it each tick and applies the requested scenario/fault
to its live DriftModels. This closes the configuration-downlink dataflow
(design §5.4) and gives the demo its one-key fault injection.

Control file schema (mutually exclusive):
    {"scenario": "tube_overheat"}            -> run a built-in scenario
    {"fault": {"target": "tube_temp", "params": {"kind": "step", "offset": 10}}}
    {"clear": true}                          -> clear all active faults

HIGH_RISK_WRITE: the P2 inspector agent must get human confirmation
before invoking the MCP tool that writes this file (design §5.1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def control_dir_for(outbox: str | Path) -> Path:
    """Control files live next to the outbox: ``<outbox>/control/``."""
    return Path(outbox) / "control"


def control_file_for(outbox: str | Path, device_type: str) -> Path:
    return control_dir_for(outbox) / f"{device_type}_fault.json"


def write_control_command(
    outbox: str | Path,
    device_type: str,
    *,
    scenario: str | None = None,
    fault: dict | None = None,
    clear: bool = False,
) -> dict:
    """MCP-side helper: atomically write a control command. Returns the command."""
    command: dict = {}
    if scenario:
        command["scenario"] = scenario
    elif fault:
        command["fault"] = fault
    elif clear:
        command["clear"] = True
    else:
        raise ValueError("one of scenario / fault / clear is required")

    cfile = control_file_for(outbox, device_type)
    cfile.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfile.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(command), encoding="utf-8")
    os.replace(tmp, cfile)
    return command


class FaultController:
    """Simulator-side watcher: apply commands from the control file once each."""

    def __init__(self, outbox: str | Path, device_type: str) -> None:
        self._cfile = control_file_for(outbox, device_type)
        self._last_mtime: float | None = None
        self.last_command: dict | None = None

    def poll(self, engine, models: dict) -> dict | None:
        """Check the control file; apply changes. Returns the applied command or None."""
        try:
            mtime = self._cfile.stat().st_mtime
        except OSError:
            return None
        if self._last_mtime is not None and mtime == self._last_mtime:
            return None
        self._last_mtime = mtime
        try:
            command = json.loads(self._cfile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        self.last_command = command

        if command.get("clear"):
            for model in models.values():
                model.clear_fault()
            return command

        if command.get("scenario"):
            engine.load_scenario_file(command["scenario"], models)
            return command

        fault = command.get("fault")
        if fault:
            target = fault.get("target")
            params = fault.get("params", {})
            model = models.get(target)
            if model is not None:
                model.inject_fault(params)
        return command
