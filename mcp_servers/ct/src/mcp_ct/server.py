"""CT MCP server: DICOM dir / tube stats / PACS connectivity tools.

Metrics come from the simulator's metrics snapshot file (P1 file channel).
NOTE: all device data is SIMULATED (毕业设计 / open-source demo).
"""

from __future__ import annotations

from pathlib import Path

from mcp_fw import MedopsMCPServer
from mcp_fw.control import make_set_fault_scenario_tool
from mcp_fw.snapshot import evaluate_thresholds, list_dir_files, read_metrics_snapshot
from medops_engine.dicom_writer import validate_dicom

SERVER_NAME = "medops-ct"

# Threshold rules for CT metrics (design §7; tube overheat scenario).
_THRESHOLD_RULES: dict[str, dict[str, float]] = {
    "tube_temp": {"warn_high": 40.0, "error_high": 45.0},
}


class CtServer(MedopsMCPServer):
    def __init__(self, outbox: str | Path = "outbox") -> None:
        super().__init__(SERVER_NAME)
        self._outbox = Path(outbox)
        self.register_tool(self.check_dicom_dir)
        self.register_tool(self.get_tube_stats)
        self.register_tool(self.check_pacs_connectivity)
        self.register_tool(
            make_set_fault_scenario_tool(outbox, "ct"), name="set_fault_scenario"
        )

    def check_dicom_dir(self, directory: str = "") -> dict:
        """Validate a DICOM directory: count valid/corrupt .dcm files."""
        target = Path(directory) if directory else self._outbox
        listing = list_dir_files(target)
        if not listing["exists"]:
            return {
                "directory": str(target),
                "exists": False,
                "total": 0,
                "valid": 0,
                "corrupt": 0,
                "simulated": True,
            }
        valid = 0
        corrupt = 0
        dcm_files = [f["name"] for f in listing["files"] if f["name"].endswith(".dcm")]
        for name in dcm_files:
            if validate_dicom(target / name):
                valid += 1
            else:
                corrupt += 1
        return {
            "directory": str(target),
            "exists": True,
            "total": len(dcm_files),
            "valid": valid,
            "corrupt": corrupt,
            "latest_mtime": max(
                (f["mtime"] for f in listing["files"] if f["name"].endswith(".dcm")),
                default=None,
            ),
            "simulated": True,
        }

    def get_tube_stats(self) -> dict:
        """Current tube metrics from the simulator snapshot + threshold status."""
        snapshot = read_metrics_snapshot(self._outbox)
        if snapshot is None:
            return {
                "available": False,
                "reason": f"no metrics snapshot in {self._outbox} (simulator running?)",
                "simulated": True,
            }
        metrics = {
            k: v
            for k, v in snapshot["metrics"].items()
            if k.startswith("tube_")
        }
        statuses = evaluate_thresholds(metrics, _THRESHOLD_RULES)
        overall = (
            "error"
            if "error" in statuses.values()
            else "warning" if "warning" in statuses.values()
            else "ok"
        )
        return {
            "available": True,
            "device_id": snapshot["device_id"],
            "ts": snapshot["ts"],
            "metrics": metrics,
            "status": overall,
            "per_metric": statuses,
            "simulated": True,
        }

    def check_pacs_connectivity(self, host: str = "127.0.0.1", port: int = 11112) -> dict:
        """TCP connect probe to the (simulated) PACS. Never raises."""
        import socket

        try:
            with socket.create_connection((host, port), timeout=2.0):
                reachable = True
                reason = "tcp connect ok"
        except OSError as exc:
            reachable = False
            reason = str(exc)
        return {
            "host": host,
            "port": port,
            "reachable": reachable,
            "reason": reason,
            "simulated": True,
        }


def build_server(outbox: str | Path = "outbox") -> CtServer:
    """Factory used by tests and the CLI entry point."""
    return CtServer(outbox=outbox)
