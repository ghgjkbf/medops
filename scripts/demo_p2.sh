#!/usr/bin/env bash
# medops P2 demo: smart layer — fault injection, scheduler inspection, alert
# escalation, work order, secretary Q&A with tool trajectory.
# Requires: PG running (deploy/README-pg.md). LLM optional (MEDOPS_LLM_MODE=fake).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

WORK="$(mktemp -d "$LOCALAPPDATA/Temp/medops-p2-demo.XXXXXX" 2>/dev/null || mktemp -d)"
PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT

export MEDOPS_LLM_MODE="${MEDOPS_LLM_MODE:-fake}"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://medops:medops@127.0.0.1:55432/medops}"
export MEDOPS_INSPECT_SECONDS=20

echo "==> 1/8 database + schema"
uv run alembic -c core/alembic.ini upgrade head

echo "==> 2/8 starting ct simulator (scenario + log file + outbox)"
uv run python -m medops_sim ct \
  --outbox "$WORK/ct" --log-file "$WORK/ct/ct.log" > "$WORK/sim.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 40); do
  [ -f "$WORK/ct/metrics_snapshot.json" ] && break
  sleep 0.5
done
[ -f "$WORK/ct/metrics_snapshot.json" ] && echo "   metrics snapshot OK" || { echo "   FAIL: no snapshot"; exit 1; }

echo "==> 3/8 starting MCP servers (ct:8801, maintenance-db:8805)"
uv run python -m mcp_ct --port 8801 --outbox "$WORK/ct" > "$WORK/mcp-ct.log" 2>&1 &
PIDS+=($!)
uv run python -m mcp_maintenance_db --port 8805 > "$WORK/mcp-db.log" 2>&1 &
PIDS+=($!)

probe() {
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
for p in 8801 8805; do probe "$p" && echo "   port $p ready"; done

echo "==> 4/8 register servers into mcp_server table"
uv run python - << 'EOF'
from medops_core.mcp_client.sync import RegistrySync

sync = RegistrySync()
sync.upsert_server("ct", "http://127.0.0.1:8801/mcp", "unknown", [])
sync.upsert_server("maintenance-db", "http://127.0.0.1:8805/mcp", "unknown", [])
print("   registered: ct, maintenance-db")
EOF

echo "==> 5/8 starting core (lifespan: registry connect + inspection scheduler)"
PYTHONPATH=core/src MEDOPS_INSPECT_SECONDS=20 uv run uvicorn medops_core.app:app --port 8123 > "$WORK/core.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 40); do
  if curl -s http://127.0.0.1:8123/api/v1/health > /dev/null 2>&1; then break; fi
  sleep 0.5
done
curl -s http://127.0.0.1:8123/api/v1/health
echo ""

echo "==> 6/8 inject a fault via MCP write path, wait for metrics to drift"
uv run python - << 'EOF'
import asyncio
from medops_core.mcp_client.registry import MCPServerConfig, MCPRegistry

async def main():
    reg = MCPRegistry()
    reg.register(MCPServerConfig(name="ct", url="http://127.0.0.1:8801/mcp"))
    await reg.connect_all()
    r = await reg.call_tool("ct", "set_fault_scenario",
                            {"fault": {"target": "tube_temp",
                                       "params": {"kind": "step", "offset": 25}}})
    print(f"   set_fault_scenario accepted={r['accepted']} risk={r['action_risk']}")
asyncio.run(main())
EOF
for _ in $(seq 1 20); do
  CUR_TEMP=$(curl -s http://127.0.0.1:8801/mcp -o /dev/null 2>/dev/null; uv run python -c "
import json
print(json.load(open(r'$WORK/ct/metrics_snapshot.json', encoding='utf-8'))['metrics']['tube_temp'])
" 2>/dev/null || echo 0)
  if [ "${CUR_TEMP%%.*}" -ge 55 ] 2>/dev/null; then break; fi
  sleep 1
done
echo "   tube_temp drifted to ${CUR_TEMP}C (baseline ~35)"

echo "==> 7/8 manual inspection via API (attribution + alert + work order)"
curl -s -X POST http://127.0.0.1:8123/api/v1/agents/inspect
echo ""

echo "==> 8/8 secretary Q&A via API (tool trajectory visible)"
curl -s -X POST http://127.0.0.1:8123/api/v1/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"3号CT球管温度多少\"}"
echo ""

echo "==> verify alerts + work orders + chat persisted in PG"
uv run python - << 'EOF'
import asyncio
from sqlalchemy import select, func
from medops_core.db import make_engine, make_session_factory
from medops_core.models import Alert, WorkOrder, ChatMessage

async def main():
    engine = make_engine()
    factory = make_session_factory(engine)
    async with factory() as s:
        n_alerts = await s.scalar(select(func.count()).select_from(Alert))
        n_orders = await s.scalar(select(func.count()).select_from(WorkOrder))
        n_msgs = await s.scalar(select(func.count()).select_from(ChatMessage))
        print(f"   alerts={n_alerts} work_orders={n_orders} chat_messages={n_msgs}")
        assert n_alerts >= 1, "no alerts stored"
        assert n_orders >= 1, "no work order stored"
        assert n_msgs >= 2, "chat not persisted"
    await engine.dispose()
    print("   persistence OK")
asyncio.run(main())
EOF

uv run pytest -q
echo ""
echo "P2 demo OK"
