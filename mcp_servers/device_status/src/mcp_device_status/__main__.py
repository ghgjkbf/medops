"""CLI entry: ``python -m mcp_device_status --transport http --port 8765``.

NOTE: all device data served here is simulated (synthetic data only).
"""

import argparse
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp-device-status")
    parser.add_argument("--transport", choices=["http", "stdio"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    from mcp_device_status.server import build_server

    server = build_server()
    if args.transport == "stdio":
        server.mcp.run("stdio")
    else:
        asyncio.run(server.serve_http(host=args.host, port=args.port))


if __name__ == "__main__":
    main()
