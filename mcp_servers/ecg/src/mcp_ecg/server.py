"""ECG MCP server: waveform quality / export-file checks.

Metrics come from the simulator's metrics snapshot file (P1 file channel).
NOTE: all device data is simulated (synthetic data only).
"""

from __future__ import annotations

from pathlib import Path

from mcp_fw import MedopsMCPServer
from mcp_fw.control import make_set_fault_scenario_tool
from mcp_fw.snapshot import evaluate_thresholds, list_dir_files, read_metrics_snapshot

SERVER_NAME = "medops-ecg"

_THRESHOLD_RULES: dict[str, dict[str, float]] = {
    "waveform_snr": {"warn_low": 20.0, "error_low": 12.0},
    "battery_voltage": {"warn_low": 11.5, "error_low": 11.0},
}

# Simulated lead set (P1); real devices would read electrode impedance.
_SIMULATED_LEADS = ["I", "II", "III", "aVR", "aVL", "aVF", "V1-V6"]


class EcgServer(MedopsMCPServer):
    def __init__(self, outbox: str | Path = "outbox") -> None:
        super().__init__(SERVER_NAME)
        self._outbox = Path(outbox)
        self.register_tool(self.get_waveform_quality)
        self.register_tool(self.check_export_files)
        self.register_tool(
            make_set_fault_scenario_tool(outbox, "ecg"), name="set_fault_scenario"
        )

    def get_waveform_quality(self) -> dict:
        """Waveform SNR + (simulated) lead attach status from the snapshot."""
        snapshot = read_metrics_snapshot(self._outbox)
        if snapshot is None:
            return {
                "available": False,
                "reason": f"no metrics snapshot in {self._outbox} (simulator running?)",
                "simulated": True,
            }
        snr = snapshot["metrics"].get("waveform_snr")
        statuses = evaluate_thresholds(
            {"waveform_snr": snr} if snr is not None else {}, _THRESHOLD_RULES
        )
        status = statuses.get("waveform_snr", "ok")
        # Simulated lead status: all attached while SNR ok, leads off on error.
        leads = (
            {lead: "off" for lead in _SIMULATED_LEADS}
            if status == "error"
            else {lead: "attached" for lead in _SIMULATED_LEADS}
        )
        return {
            "available": snr is not None,
            "device_id": snapshot["device_id"],
            "ts": snapshot["ts"],
            "waveform_snr": snr,
            "status": status,
            "leads": leads,
            "simulated": True,
        }

    def check_export_files(self, directory: str = "") -> dict:
        """ECG export directory integrity (files present + sizes/mtimes)."""
        target = Path(directory) if directory else self._outbox
        listing = list_dir_files(target)
        if not listing["exists"]:
            return {
                "directory": str(target),
                "exists": False,
                "count": 0,
                "simulated": True,
            }
        files = listing["files"]
        return {
            "directory": str(target),
            "exists": True,
            "count": len(files),
            "files": files,
            "simulated": True,
        }


def build_server(outbox: str | Path = "outbox") -> EcgServer:
    """Factory used by tests and the CLI entry point."""
    return EcgServer(outbox=outbox)
