#!/usr/bin/env bash
# medops P1 demo: simulator -> MCP servers -> registry -> fault injection -> work order.
# Requires: PG running (deploy/README-pg.md), uv, no Docker needed.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

WORK="$(mktemp -d "$LOCALAPPDATA/Temp/medops-p1-demo.XXXXXX" 2>/dev/null || mktemp -d)"
PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "==> 1/7 database ready"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://medops:medops@127.0.0.1:55432/medops}"
uv run alembic -c core/alembic.ini upgrade head

echo "==> 2/7 starting ct + ventilator simulators (outbox=$WORK)"
uv run python -m medops_sim ct --scenario tube_overheat --outbox "$WORK/ct" > "$WORK/ct.log" 2>&1 &
PIDS+=($!)
uv run python -m medops_sim ventilator --outbox "$WORK/vent" > "$WORK/vent.log" 2>&1 &
PIDS+=($!)
sleep 3
grep -q "metrics_snapshot" /dev/null 2>/dev/null || true
[ -f "$WORK/ct/metrics_snapshot.json" ] && echo "   ct snapshot OK"
[ -f "$WORK/vent/metrics_snapshot.json" ] && echo "   ventilator snapshot OK"

echo "==> 3/7 starting MCP servers (ct:8801, ventilator:8803, maintenance-db:8805)"
uv run python -m mcp_ct --port 8801 --outbox "$WORK/ct" > "$WORK/mcp-ct.log" 2>&1 &
PIDS+=($!)
uv run python -m mcp_ventilator --port 8803 --outbox "$WORK/vent" > "$WORK/mcp-vent.log" 2>&1 &
PIDS+=($!)
uv run python -m mcp_maintenance_db --port 8805 > "$WORK/mcp-db.log" 2>&1 &
PIDS+=($!)

probe() {  # TCP readiness (bare ping to /mcp returns 400 — P0 note #6)
  local port=$1
  for _ in $(seq 1 30); do
    if uv run python -c "
import socket,sys
s=socket.socket(); s.settimeout(1)
sys.exit(0 if s.connect_ex(('127.0.0.1',$port))==0 else 1)
" 2>/dev/null; then return 0; fi
    sleep 0.5
  done
  return 1
}
for p in 8801 8803 8805; do probe "$p" && echo "   port $p ready"; done

echo "==> 4/7 registry connects + calls tools over streamable-http"
uv run python - "$WORK" << 'EOF'
import asyncio, json, sys
from medops_core.mcp_client.registry import MCPServerConfig, MCPRegistry
from medops_core.mcp_client.sync import RegistrySync

work = sys.argv[1]
reg = MCPRegistry()
for name, port in (("ct", 8801), ("ventilator", 8803), ("maintenance-db", 8805)):
    reg.register(MCPServerConfig(name=name, url=f"http://127.0.0.1:{port}/mcp"))

async def main():
    await reg.connect_all()
    for h in reg._handles.values():
        print(f"   {h.config.name}: {h.state.value}, tools={h.tools}")
    tube = await reg.call_tool("ct", "get_tube_stats")
    print(f"   ct tube_temp={tube['metrics']['tube_temp']:.2f} status={tube['status']}")
    # fault injection via MCP write path
    r = await reg.call_tool("ct", "set_fault_scenario",
                            {"fault": {"target": "tube_temp",
                                       "params": {"kind": "step", "offset": 30}}})
    print(f"   set_fault_scenario accepted={r['accepted']} risk={r['action_risk']}")
asyncio.run(main())
EOF

echo "==> 5/7 waiting for simulator to apply the injected fault"
sleep 4
uv run python - "$WORK" << 'EOF'
import json, sys
snap = json.loads(open(sys.argv[1] + "/ct/metrics_snapshot.json", encoding="utf-8").read())
print(f"   ct tube_temp after fault = {snap['metrics']['tube_temp']:.2f} (baseline ~35)")
assert snap["metrics"]["tube_temp"] > 55, "fault was not applied"
print("   fault confirmed drifting")
EOF

echo "==> 6/7 maintenance-db creates a work order for the fault"
uv run python - << 'EOF'
import asyncio
from medops_core.mcp_client.registry import MCPServerConfig, MCPRegistry

reg = MCPRegistry()
reg.register(MCPServerConfig(name="maintenance-db", url="http://127.0.0.1:8805/mcp"))

async def main():
    await reg.connect_all()
    r = await reg.call_tool("maintenance-db", "create_work_order", {
        "device_id": "ct-sim-01",
        "title": "Tube overheat detected by inspector",
        "dedupe_key": "p1-demo-wo",
    })
    print(f"   work order id={r['work_order_id']} created={r['created']} risk={r['action_risk']}")
asyncio.run(main())
EOF

echo "==> 7/7 final gate: full test suite"
uv run pytest -q

echo ""
echo "P1 demo OK"
