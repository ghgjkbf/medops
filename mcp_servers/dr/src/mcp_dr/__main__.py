"""CLI entry: ``python -m mcp_dr --transport http --port 8802``.

NOTE: all device data served here is SIMULATED (毕业设计 / open-source demo).
"""

import argparse
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp-dr")
    parser.add_argument("--transport", choices=["http", "stdio"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8802)
    parser.add_argument(
        "--outbox",
        default="outbox",
        help="simulator outbox dir to read metrics_snapshot.json from",
    )
    args = parser.parse_args()

    from mcp_dr.server import build_server

    server = build_server(outbox=args.outbox)
    if args.transport == "stdio":
        server.mcp.run("stdio")
    else:
        asyncio.run(server.serve_http(host=args.host, port=args.port))


if __name__ == "__main__":
    main()
