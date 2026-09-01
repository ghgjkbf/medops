"""mcp-fw: MCP Server template base framework.

Public API:
    MedopsMCPServer — base class for all medops MCP servers.

NOTE: all data served by medops MCP servers is SIMULATED
(毕业设计 / open-source demo — no real medical devices attached).
"""

from mcp_fw.base import MedopsMCPServer

__version__ = "0.1.0"

__all__ = ["MedopsMCPServer"]
