"""Device-status MCP server: process / driver / file-integrity tools.

NOTE: all device data is simulated (synthetic data only —
no real medical devices attached).
"""

import os
from datetime import UTC, datetime

import psutil
from mcp_fw import MedopsMCPServer
from mcp_fw.base import HeartbeatProvider

SERVER_NAME = "medops-device-status"

# Simulated device registry + driver table (P0).
# 真实环境：device 注册表来自 engine/devices 层，驱动信息应对接
# Windows WMI (Win32_PnPSignedDriver) —— 此处仅静态模拟。
_SIMULATED_DEVICES: dict[str, str] = {
    "CT-001": "ct",
    "DR-001": "dr",
    "VENT-001": "ventilator",
    "ECG-001": "ecg",
}

_SIMULATED_DRIVERS: dict[str, dict] = {
    "ct": {"name": "SimCT AcqDriver", "version": "4.2.1-sim", "status": "running"},
    "dr": {"name": "SimDR PanelDriver", "version": "2.8.0-sim", "status": "running"},
    "ventilator": {"name": "SimVent FlowDriver", "version": "1.5.3-sim", "status": "running"},
    "ecg": {"name": "SimECG LeadDriver", "version": "3.1.0-sim", "status": "running"},
}


class DeviceStatusServer(MedopsMCPServer):
    """MCP server exposing simulated device-status tools."""

    def __init__(self, heartbeat_provider: HeartbeatProvider | None = None) -> None:
        super().__init__(SERVER_NAME, heartbeat_provider=heartbeat_provider)
        self.register_tool(self.get_process_status)
        self.register_tool(self.get_driver_info)
        self.register_tool(self.get_file_integrity)

    # ------------------------------------------------------------------ tools
    def get_process_status(self, process_name: str = "medops-sim") -> dict:
        """Check whether a (simulator) process is alive via psutil.

        Matches against process name and cmdline (case-insensitive substring).
        """
        name_l = process_name.lower()
        matches = []
        for proc in psutil.process_iter(["pid", "name", "status", "memory_info"]):
            try:
                info = proc.info
                cmdline = " ".join(proc.cmdline())
                if name_l in (info["name"] or "").lower() or name_l in cmdline.lower():
                    matches.append(
                        {
                            "pid": info["pid"],
                            "name": info["name"],
                            "status": info["status"],
                            "cpu_percent": proc.cpu_percent(interval=None),
                            "memory_mb": round((info["memory_info"].rss or 0) / 1024 / 1024, 1),
                        }
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return {
            "process_name": process_name,
            "found": len(matches) > 0,
            "count": len(matches),
            "processes": matches[:10],
        }

    def get_driver_info(self, device_id: str) -> dict:
        """Return (simulated) driver info for a device id, e.g. 'CT-001'.

        P0: static simulated table. 真实环境对接 Windows WMI
        (Win32_PnPSignedDriver) 查询驱动版本与签名状态。
        """
        device_type = _SIMULATED_DEVICES.get(device_id)
        if device_type is None:
            return {"device_id": device_id, "found": False, "simulated": True}
        driver = _SIMULATED_DRIVERS[device_type]
        return {
            "device_id": device_id,
            "found": True,
            "device_type": device_type,
            "driver": dict(driver),
            "simulated": True,
        }

    def get_file_integrity(self, path: str, expected_min_bytes: int = 1) -> dict:
        """Check a log/data file exists and meets a minimum size."""
        if not os.path.isfile(path):
            return {
                "path": path,
                "exists": False,
                "ok": False,
                "size_bytes": 0,
                "mtime": None,
                "expected_min_bytes": expected_min_bytes,
            }
        st = os.stat(path)
        size = st.st_size
        return {
            "path": path,
            "exists": True,
            "ok": size >= expected_min_bytes,
            "size_bytes": size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
            "expected_min_bytes": expected_min_bytes,
        }


def build_server(heartbeat_provider: HeartbeatProvider | None = None) -> DeviceStatusServer:
    """Factory used by tests and the CLI entry point."""
    return DeviceStatusServer(heartbeat_provider=heartbeat_provider)
