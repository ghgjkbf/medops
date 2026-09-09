"""DR MCP server: generator status / detector temperature tools.

Metrics come from the simulator's metrics snapshot file (P1 file channel).
NOTE: all device data is simulated (synthetic data only).
"""

from __future__ import annotations

from pathlib import Path

from mcp_fw import MedopsMCPServer
from mcp_fw.control import make_set_fault_scenario_tool
from mcp_fw.device_system import register_device_system_tools
from mcp_fw.snapshot import evaluate_thresholds, read_metrics_snapshot
from medops_common.thresholds import THRESHOLD_RULES as _ALL_RULES

SERVER_NAME = "medops-dr"

_THRESHOLD_RULES = {
    k: _ALL_RULES[k] for k in ("detector_temp", "generator_kvp", "disk_free_gb")
}


class DrServer(MedopsMCPServer):
    def __init__(self, outbox: str | Path = "outbox") -> None:
        super().__init__(SERVER_NAME)
        self._outbox = Path(outbox)
        self.register_tool(self.check_generator_status)
        self.register_tool(self.get_detector_temp)
        self.register_tool(
            make_set_fault_scenario_tool(outbox, "dr"), name="set_fault_scenario"
        )
        register_device_system_tools(self, outbox, "dr")

    def _snapshot_or_none(self) -> dict | None:
        return read_metrics_snapshot(self._outbox)

    def check_generator_status(self) -> dict:
        """Generator kV/mAs regulation status from the simulator snapshot."""
        snapshot = self._snapshot_or_none()
        if snapshot is None:
            return {
                "available": False,
                "reason": f"no metrics snapshot in {self._outbox} (simulator running?)",
                "simulated": True,
            }
        metrics = {
            k: v
            for k, v in snapshot["metrics"].items()
            if k.startswith("generator_")
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

    def get_detector_temp(self) -> dict:
        """Detector temperature + threshold status."""
        snapshot = self._snapshot_or_none()
        if snapshot is None:
            return {
                "available": False,
                "reason": f"no metrics snapshot in {self._outbox} (simulator running?)",
                "simulated": True,
            }
        value = snapshot["metrics"].get("detector_temp")
        statuses = evaluate_thresholds(
            {"detector_temp": value} if value is not None else {},
            _THRESHOLD_RULES,
        )
        return {
            "available": value is not None,
            "device_id": snapshot["device_id"],
            "ts": snapshot["ts"],
            "detector_temp": value,
            "status": statuses.get("detector_temp", "ok"),
            "simulated": True,
        }


def build_server(outbox: str | Path = "outbox") -> DrServer:
    """Factory used by tests and the CLI entry point."""
    return DrServer(outbox=outbox)
