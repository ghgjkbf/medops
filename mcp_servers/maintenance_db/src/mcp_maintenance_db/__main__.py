"""CLI entry: ``python -m mcp_maintenance_db --transport http --port 8805``.

NOTE: all data served here is SIMULATED (毕业设计 / open-source demo).
"""

import argparse
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp-maintenance-db")
    parser.add_argument("--transport", choices=["http", "stdio"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8805)
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL URL (default: DATABASE_URL env or medops_core.db default)",
    )
    args = parser.parse_args()

    from mcp_maintenance_db.server import build_server

    server = build_server(database_url=args.database_url)
    if args.transport == "stdio":
        server.mcp.run("stdio")
    else:
        asyncio.run(server.serve_http(host=args.host, port=args.port))


if __name__ == "__main__":
    main()
