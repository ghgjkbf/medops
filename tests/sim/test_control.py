"""P1-9: fault-scenario write path tests (control file + MCP tool + e2e)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from mcp_fw.control import make_set_fault_scenario_tool
from medops_engine.drift import DriftModel
from medops_sim.control import FaultController, control_file_for, write_control_command


class _NullEngine:
    """Scenario-less engine stand-in for unit tests."""

    def load_scenario_file(self, name, models):  # pragma: no cover
        self.loaded = name


def _null_engine() -> _NullEngine:
    return _NullEngine()


# ----------------------------------------------------------------- unit tests
def test_write_and_poll_fault_command(tmp_path: Path) -> None:
    models = {"tube_temp": DriftModel(35.0, seed=1)}
    controller = FaultController(tmp_path, "ct")

    # no control file yet -> None
    assert controller.poll(_null_engine(), models) is None

    command = write_control_command(
        tmp_path,
        "ct",
        fault={"target": "tube_temp", "params": {"kind": "step", "offset": 10}},
    )
    applied = controller.poll(_null_engine(), models)
    assert applied == command
    # same mtime -> not re-applied
    assert controller.poll(_null_engine(), models) is None


def test_clear_command(tmp_path: Path) -> None:
    models = {"tube_temp": DriftModel(35.0, seed=1)}
    models["tube_temp"].inject_fault({"kind": "step", "offset": 20})
    controller = FaultController(tmp_path, "ct")
    write_control_command(tmp_path, "ct", clear=True)
    applied = controller.poll(_null_engine(), models)
    assert applied == {"clear": True}
    # fault cleared: value back to baseline
    assert abs(models["tube_temp"].next(0.0) - 35.0) < 1e-9


def test_scenario_command_hot_loads(tmp_path: Path) -> None:
    class FakeEngine:
        loaded = None

        def load_scenario_file(self, name, models):
            self.loaded = name

    engine = FakeEngine()
    models: dict = {}
    controller = FaultController(tmp_path, "ct")
    write_control_command(tmp_path, "ct", scenario="tube_overheat")
    applied = controller.poll(engine, models)
    assert applied == {"scenario": "tube_overheat"}
    assert engine.loaded == "tube_overheat"


def test_mcp_tool_writes_control_file(tmp_path: Path) -> None:
    tool = make_set_fault_scenario_tool(tmp_path, "ct")
    result = tool(fault={"target": "tube_temp", "params": {"kind": "step", "offset": 5}})
    assert result["accepted"] is True
    assert result["action_risk"] == "high_risk_write"
    cfile = control_file_for(tmp_path, "ct")
    assert json.loads(cfile.read_text(encoding="utf-8"))["fault"]["target"] == "tube_temp"

    # no args -> rejected
    result2 = tool()
    assert result2["accepted"] is False


# ------------------------------------------------------------------- e2e test
def test_e2e_control_downlink(tmp_path: Path) -> None:
    """Simulator subprocess + MCP tool write -> metric drifts -> clear recovers."""
    outbox = tmp_path / "outbox"
    proc = subprocess.Popen(
        [sys.executable, "-m", "medops_sim", "ct", "--outbox", str(outbox)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # wait for snapshot to appear (simulator alive)
        snapshot = outbox / "metrics_snapshot.json"
        deadline = time.monotonic() + 10
        while not snapshot.is_file() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert snapshot.is_file(), "simulator did not produce a snapshot"

        baseline = json.loads(snapshot.read_text(encoding="utf-8"))["metrics"]["tube_temp"]

        # MCP-side write (what the agent's tool call does)
        tool = make_set_fault_scenario_tool(outbox, "ct")
        r = tool(fault={"target": "tube_temp", "params": {"kind": "step", "offset": 30}})
        assert r["accepted"] is True

        # wait for the simulator to apply it (next tick) and re-snapshot
        deadline = time.monotonic() + 10
        faulted = baseline
        while time.monotonic() < deadline:
            time.sleep(0.3)
            faulted = json.loads(snapshot.read_text(encoding="utf-8"))["metrics"]["tube_temp"]
            if faulted > baseline + 20:
                break
        assert faulted > baseline + 20, f"no drift: baseline={baseline} got={faulted}"

        # clear via control command
        tool(clear=True)
        deadline = time.monotonic() + 10
        recovered = faulted
        while time.monotonic() < deadline:
            time.sleep(0.3)
            recovered = json.loads(snapshot.read_text(encoding="utf-8"))["metrics"]["tube_temp"]
            if abs(recovered - baseline) < 10:
                break
        assert abs(recovered - baseline) < 10, (
            f"no recovery: baseline={baseline} got={recovered}"
        )
    finally:
        proc.terminate()
        proc.communicate(timeout=20)
