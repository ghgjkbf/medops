"""mcp-device-status: simulated device-status MCP server.

NOTE: all device data is simulated (synthetic data only —
no real medical devices attached).
"""

from mcp_device_status.server import DeviceStatusServer, build_server

__version__ = "0.1.0"

__all__ = ["DeviceStatusServer", "build_server"]
