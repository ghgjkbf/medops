"""Integration smoke tests for the medops-sim CLI (subprocess-level)."""

from __future__ import annotations

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
