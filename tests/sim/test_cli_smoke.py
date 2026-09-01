"""Integration smoke tests for the medops-sim CLI (subprocess-level)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

SIM_TIMEOUT_S = 20.0


def _run_cli(*args: str, timeout: float = SIM_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "medops_sim", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_list_scenarios() -> None:
    result = _run_cli("ct", "--list-scenarios")
    assert result.returncode == 0, result.stderr
    for name in ("tube_overheat", "o2_cell_drift", "disk_full"):
        assert name in result.stdout


def test_ct_smoke(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "medops_sim",
            "ct",
            "--scenario",
            "tube_overheat",
            "--outbox",
            str(outbox),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(2.0)
        assert proc.poll() is None, (
            f"simulator exited early rc={proc.returncode}: {proc.stderr.read()}"
        )
    finally:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=SIM_TIMEOUT_S)

    assert "[METRIC]" in stdout, f"no metric lines in stdout:\n{stdout}\n{stderr}"
    assert "[HB]" in stdout, f"no heartbeat lines in stdout:\n{stdout}"
    dcm_files = list(outbox.glob("*.dcm"))
    assert len(dcm_files) >= 1, f"no .dcm files in {outbox}"


def test_ct_metrics_snapshot(tmp_path: Path) -> None:
    """P1-3: simulator writes an atomically-updated metrics snapshot each tick."""
    import json

    outbox = tmp_path / "outbox"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "medops_sim",
            "ct",
            "--scenario",
            "tube_overheat",
            "--outbox",
            str(outbox),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(2.5)
        assert proc.poll() is None, (
            f"simulator exited early rc={proc.returncode}: {proc.stderr.read()}"
        )
    finally:
        proc.terminate()
        proc.communicate(timeout=SIM_TIMEOUT_S)

    snapshot_path = outbox / "metrics_snapshot.json"
    assert snapshot_path.is_file(), f"no metrics snapshot in {outbox}"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["device_id"] == "ct-sim-01"
    assert set(snapshot["metrics"]) == {"tube_temp", "tube_exposure_count"}
    assert isinstance(snapshot["metrics"]["tube_temp"], float)


def test_ecg_smoke(tmp_path: Path) -> None:
    """P1-3: ecg device runs with its new scenario and battery metric."""
    outbox = tmp_path / "outbox"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "medops_sim",
            "ecg",
            "--scenario",
            "ecg_battery_low",
            "--outbox",
            str(outbox),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(2.0)
        assert proc.poll() is None, (
            f"simulator exited early rc={proc.returncode}: {proc.stderr.read()}"
        )
    finally:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=SIM_TIMEOUT_S)

    assert "[METRIC] battery_voltage" in stdout, f"battery metric missing:\n{stdout}"
    assert "[METRIC] heart_rate" in stdout
    snapshot = json.loads((outbox / "metrics_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["device_id"] == "ecg-sim-01"
    assert "battery_voltage" in snapshot["metrics"]
