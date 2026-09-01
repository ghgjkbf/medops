"""Ventilator MCP server: realtime params / self-test tools.

Metrics come from the simulator's metrics snapshot file (P1 file channel).
NOTE: all device data is simulated (synthetic data only).
"""

from __future__ import annotations

from pathlib import Path

from mcp_fw import MedopsMCPServer
from mcp_fw.control import make_set_fault_scenario_tool
from mcp_fw.snapshot import evaluate_thresholds, read_metrics_snapshot

SERVER_NAME = "medops-ventilator"

_THRESHOLD_RULES: dict[str, dict[str, float]] = {
    "o2_concentration": {"warn_low": 90.0, "error_low": 85.0},
    "tidal_volume": {"warn_low": 350.0, "error_low": 250.0},
    "airway_pressure": {"warn_low": 10.0, "error_low": 8.0},
}


class VentilatorServer(MedopsMCPServer):
    def __init__(self, outbox: str | Path = "outbox") -> None:
        super().__init__(SERVER_NAME)
        self._outbox = Path(outbox)
        self.register_tool(self.get_realtime_params)
        self.register_tool(self.run_self_test)
        self.register_tool(
            make_set_fault_scenario_tool(outbox, "ventilator"), name="set_fault_scenario"
        )

    def get_realtime_params(self) -> dict:
        """Current ventilation parameters + threshold status."""
        snapshot = read_metrics_snapshot(self._outbox)
        if snapshot is None:
            return {
                "available": False,
                "reason": f"no metrics snapshot in {self._outbox} (simulator running?)",
                "simulated": True,
            }
        params = snapshot["metrics"]
        statuses = evaluate_thresholds(params, _THRESHOLD_RULES)
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
            "params": params,
            "status": overall,
            "per_metric": statuses,
            "simulated": True,
        }

    def run_self_test(self) -> dict:
        """Deterministic self-check: subsystems fail if their metric is out of range."""
        snapshot = read_metrics_snapshot(self._outbox)
        if snapshot is None:
            return {
                "available": False,
                "reason": f"no metrics snapshot in {self._outbox} (simulator running?)",
                "simulated": True,
            }
        params = snapshot["metrics"]
        o2 = params.get("o2_concentration", 93.0)
        vt = params.get("tidal_volume", 500.0)
        paw = params.get("airway_pressure", 15.0)
        subsystems = {
            "o2_cell": "pass" if o2 > 85.0 else "fail",
            "flow_sensor": "pass" if vt > 250.0 else "fail",
            "pressure_circuit": "pass" if paw > 8.0 else "fail",
            "power_supply": "pass",  # no battery metric on ventilator in P1
        }
        return {
            "available": True,
            "device_id": snapshot["device_id"],
            "subsystems": subsystems,
            "overall": "fail" if "fail" in subsystems.values() else "pass",
            "simulated": True,
        }


def build_server(outbox: str | Path = "outbox") -> VentilatorServer:
    """Factory used by tests and the CLI entry point."""
    return VentilatorServer(outbox=outbox)
