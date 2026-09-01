"""P1-3: full scenario library (3 per device) + metrics snapshot format."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from medops_common.constants import DeviceType
from medops_engine.scenario import ScenarioEngine
from medops_sim import __main__ as sim

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO_ROOT / "devices" / "engine" / "scenarios"

EXPECTED_SCENARIOS = {
    "tube_overheat": DeviceType.CT,
    "ct_pacs_disconnect": DeviceType.CT,
    "o2_cell_drift": DeviceType.VENTILATOR,
    "ventilator_leak": DeviceType.VENTILATOR,
    "disk_full": DeviceType.DR,
    "dr_generator_overheat": DeviceType.DR,
    "dr_detector_cooling": DeviceType.DR,
    "ecg_lead_off": DeviceType.ECG,
    "ecg_battery_low": DeviceType.ECG,
}


@pytest.mark.parametrize("name,device", sorted(EXPECTED_SCENARIOS.items()))
def test_scenario_loads(name: str, device: DeviceType) -> None:
    engine = ScenarioEngine.load(SCENARIO_DIR / f"{name}.yaml")
    assert engine.scenario.scenario == name
    assert engine.scenario.device == device
    assert engine.scenario.duration_s > 0
    assert len(engine.scenario.events) >= 2


def test_nine_scenarios_on_disk() -> None:
    yaml_files = {p.stem for p in SCENARIO_DIR.glob("*.yaml")}
    assert yaml_files == set(EXPECTED_SCENARIOS)


@pytest.mark.parametrize(
    "name",
    ["ecg_lead_off", "ecg_battery_low", "ct_pacs_disconnect",
     "dr_generator_overheat", "ventilator_leak", "dr_detector_cooling"],
)
def test_new_scenario_event_order(name: str) -> None:
    """Each new scenario follows the inject/log -> warning -> error -> clear arc."""
    engine = ScenarioEngine.load(SCENARIO_DIR / f"{name}.yaml")
    kinds = [e.action for e in engine.scenario.events]
    assert kinds[0] in ("inject_fault", "log")
    if "log" in kinds:
        levels = [
            e.params.get("level")
            for e in engine.scenario.events
            if e.action == "log"
        ]
        assert "warning" in levels and "error" in levels
    assert kinds[-1] == "log"  # closing info log after clear/last event
    assert "clear_fault" in kinds or kinds[0] == "log"


def test_log_only_scenario_has_no_fault() -> None:
    """ct_pacs_disconnect must be a pure-log scenario (no metric drift)."""
    engine = ScenarioEngine.load(SCENARIO_DIR / "ct_pacs_disconnect.yaml")
    assert all(e.action != "inject_fault" for e in engine.scenario.events)
    assert all(e.action != "clear_fault" for e in engine.scenario.events)


def test_dr_generator_scenario_targets_new_metrics() -> None:
    """dr_generator_overheat drives the new generator_kvp metric."""
    engine = ScenarioEngine.load(SCENARIO_DIR / "dr_generator_overheat.yaml")
    targets = {e.target for e in engine.scenario.events if e.action == "inject_fault"}
    assert "generator_kvp" in targets


def test_new_device_metrics_in_configs() -> None:
    assert "generator_kvp" in sim.DEVICE_CONFIGS["dr"]
    assert "generator_mas" in sim.DEVICE_CONFIGS["dr"]
    assert "battery_voltage" in sim.DEVICE_CONFIGS["ecg"]


def test_write_metrics_snapshot_format(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    sim.write_metrics_snapshot(
        outbox, "ct-sim-01", 1.5, {"tube_temp": 35.2, "tube_exposure_count": 0.0}
    )
    snapshot = json.loads((outbox / "metrics_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["device_id"] == "ct-sim-01"
    assert snapshot["t"] == 1.5
    assert snapshot["metrics"]["tube_temp"] == 35.2
    assert "ts" in snapshot  # ISO timestamp string

    # Update overwrites (atomic replace), no .tmp leftover.
    sim.write_metrics_snapshot(outbox, "ct-sim-01", 2.5, {"tube_temp": 35.9})
    snapshot2 = json.loads((outbox / "metrics_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot2["t"] == 2.5
    assert snapshot2["metrics"]["tube_temp"] == 35.9
    assert not list(outbox.glob("*.tmp"))


def test_cli_list_scenarios_nine(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "medops_sim", "ct", "--list-scenarios"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    for name in EXPECTED_SCENARIOS:
        assert name in proc.stdout
