#!/usr/bin/env bash
# ============================================================
# medops P3 full-chain simulation experiment (scripts/experiment_p3.sh)
# Sim cold start -> MCP register -> backend restart -> baseline inspect
# -> MCP fault injection -> inspect -> alert/work-order/WS/chat/report
# -> golden eval suite. Requires backend via scripts/start.bat (or starts it).
# ============================================================
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)"
cd "$REPO"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://medops:medops@127.0.0.1:55432/medops}"
export MEDOPS_LLM_MODE="${MEDOPS_LLM_MODE:-fake}"
WORK="$(mktemp -d "$LOCALAPPDATA/Temp/medops-exp.XXXXXX" 2>/dev/null || mktemp -d)"
PIDS=(); cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$WORK";
  # MSYS kill cannot stop native Windows children reliably — sweep by port + cmdline
  powershell -NoProfile -Command "foreach (\$port in 8801,8803,8805) { Get-NetTCPConnection -LocalPort \$port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id \$_ -Force -ErrorAction SilentlyContinue } }; Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -match 'mcp_ct|mcp_ventilator|mcp_maintenance_db|medops_sim' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" > /dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== [0/8] sweep leftover sim/MCP processes from previous runs =="
powershell -NoProfile -Command "foreach (\$port in 8801,8803,8805) { Get-NetTCPConnection -LocalPort \$port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id \$_ -Force -ErrorAction SilentlyContinue } }; Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -match 'mcp_ct|mcp_ventilator|mcp_maintenance_db|medops_sim' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" > /dev/null 2>&1 || true
sleep 1

PGBIN="${PGBIN:-/d/ai-use/tools/pg16/pgsql/bin}"
echo "== [1/8] prereqs: PostgreSQL + backend =="
"$PGBIN/pg_isready.exe" -h 127.0.0.1 -p 55432 > /dev/null
if ! curl -sf -o /dev/null --max-time 3 http://127.0.0.1:8123/api/v1/health; then
  echo "   backend down -> quick-start it"
  MEDOPS_NO_BROWSER=1 cmd /c "scripts\\start.bat" > /dev/null 2>&1 &
fi
for i in $(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:8123/api/v1/health && break; sleep 1; done
echo "   backend health OK"

echo "== [2/8] simulators (ct baseline, ventilator) + MCP servers =="
uv run python -m medops_sim ct --outbox "$WORK/ct" > "$WORK/ct.log" 2>&1 & PIDS+=($!)
uv run python -m medops_sim ventilator --outbox "$WORK/vent" > "$WORK/vent.log" 2>&1 & PIDS+=($!)
uv run python -m mcp_ct --port 8801 --outbox "$WORK/ct" > "$WORK/mcp-ct.log" 2>&1 & PIDS+=($!)
uv run python -m mcp_ventilator --port 8803 --outbox "$WORK/vent" > "$WORK/mcp-vent.log" 2>&1 & PIDS+=($!)
uv run python -m mcp_maintenance_db --port 8805 > "$WORK/mcp-db.log" 2>&1 & PIDS+=($!)
for p in 8801 8803 8805; do
  for i in $(seq 1 30); do
    uv run python -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1',$p))==0 else 1)" 2>/dev/null && break
    sleep 0.5
  done
  echo "   port $p ready"
done

echo "== [3/8] register MCP servers into DB registry =="
uv run python - << 'EOF'
import asyncio
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig
from medops_core.mcp_client.sync import RegistrySync

reg = MCPRegistry()
for name, port in (("ct", 8801), ("ventilator", 8803), ("maintenance-db", 8805)):
    reg.register(MCPServerConfig(name=name, url=f"http://127.0.0.1:{port}/mcp"))

async def main():
    await reg.connect_all()
    # TCP-ready != MCP-protocol-ready: retry connects until healthy (~30s cap)
    for _attempt in range(15):
        unhealthy = [h for h in reg._handles.values() if h.state.value != "healthy"]
        if not unhealthy:
            break
        for h in unhealthy:
            try:
                await h.connect()
            except Exception:  # noqa: BLE001, S110 - retry next round
                pass
        await asyncio.sleep(1.0)
    sync = RegistrySync()
    for h in reg._handles.values():
        tools = list(h.tools)
        if not tools:
            print(f"   WARNING {h.config.name}: no tools (state={h.state.value})")
            print(f"   DEBUG {h.config.name} last_error: {str(h.last_error)[:400]}")
        sync.upsert_server(h.config.name, h.config.url, h.state.value, tools)
        print(f"   {h.config.name}: {len(tools)} tools, health={h.state.value}")

asyncio.run(main())
EOF
echo "   restarting backend to load registry from DB ..."
MEDOPS_NO_BROWSER=1 cmd /c "scripts\\start.bat" > /dev/null 2>&1 &
# start.bat kills the old backend mid-flight: wait for DOWN, then for UP
for i in $(seq 1 30); do curl -sf --max-time 2 -o /dev/null http://127.0.0.1:8123/api/v1/health 2>/dev/null || break; sleep 1; done
for i in $(seq 1 40); do curl -sf -o /dev/null http://127.0.0.1:8123/api/v1/health && break; sleep 1; done
echo "   backend reloaded"

echo "== [4/8] WS listener on /ws/dashboard (background) =="
uv run python - "$WORK/ws_events.jsonl" << 'EOF' &
import json, sys, threading
from websockets.sync.client import connect as ws_connect

out = open(sys.argv[1], "w", encoding="utf-8")
def listen():
    try:
        with ws_connect("ws://127.0.0.1:8123/ws/dashboard") as ws:
            while True:
                try:
                    msg = ws.recv(timeout=120)
                except Exception as e:
                    out.write(json.dumps({"type": "listener-note",
                                          "error": repr(e)}) + "\n")
                    out.flush()
                    continue
                out.write(json.dumps(json.loads(msg), ensure_ascii=False) + "\n")
                out.flush()
    except Exception as e:
        out.write(json.dumps({"type": "listener-dead", "error": repr(e)}) + "\n")
        out.flush()
t = threading.Thread(target=listen, daemon=True); t.start(); t.join(999)
EOF
PIDS+=($!)
sleep 1
echo "   listener armed"

echo "== [5/8] BASELINE inspection (expect healthy) =="
curl -sf -X POST http://127.0.0.1:8123/api/v1/agents/inspect | python -c "import json,sys; d=json.load(sys.stdin); print('   baseline:', json.dumps(d.get('data', d), ensure_ascii=False)[:220])"

echo "== [6/8] fault injection via MCP (ct tube_temp step +30) =="
uv run python - << 'EOF'
import asyncio
from medops_core.mcp_client.registry import MCPRegistry, MCPServerConfig

reg = MCPRegistry()
reg.register(MCPServerConfig(name="ct", url="http://127.0.0.1:8801/mcp"))

async def main():
    await reg.connect_all()
    r = await reg.call_tool("ct", "set_fault_scenario",
                            {"fault": {"target": "tube_temp",
                                       "params": {"kind": "step", "offset": 30}}})
    print(f"   accepted={r['accepted']} risk={r['action_risk']}")
asyncio.run(main())
EOF
sleep 5   # let the sim apply the drift
echo "   inspection after fault:"
curl -sf -X POST http://127.0.0.1:8123/api/v1/agents/inspect | python -c "import json,sys; d=json.load(sys.stdin); print('   post-fault:', json.dumps(d.get('data', d), ensure_ascii=False)[:220])"

echo "== [7/8] verify chain: alerts / work-orders / WS / chat / report =="
uv run python - "$WORK" << 'EOF'
import json, sys, urllib.request

BASE = "http://127.0.0.1:8123"
def get(p):
    with urllib.request.urlopen(BASE + p, timeout=10) as r:
        return json.load(r)
def post(p, body=None):
    req = urllib.request.Request(BASE + p, method="POST",
                                 data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)

alerts = get("/api/v1/alerts?device_id=ct-sim-01&page_size=5")["data"]["items"]
latest = alerts[0] if alerts else {}
print(f"   alert: id={latest.get('id')} level={latest.get('level')} kind={latest.get('kind')}")
print(f"          msg={str(latest.get('message'))[:80]}")

orders = get("/api/v1/work-orders?device_id=ct-sim-01&page_size=5")["data"]["items"]
wo = orders[0] if orders else {}
print(f"   work-order: id={wo.get('id')} status={wo.get('status')} title={str(wo.get('title'))[:60]}")

chat = post("/api/v1/chat", {"message": "3号CT的球管温度现在正常吗？"})
ans = chat.get("answer") or chat.get("data", {}).get("answer", "")
trace = chat.get("trajectory") or chat.get("data", {}).get("trajectory", [])
print(f"   chat: {str(ans)[:90]}")
print(f"   chat tool trace: {[t.get('tool') for t in trace][:6]}")

rep = post("/api/v1/reports/generate?hours=1")
md = rep.get("markdown") or rep.get("data", {}).get("markdown", "")
print(f"   report: {len(md)} chars, head: {md[:100].strip()!r}")
EOF
echo "   WS events captured: $(wc -l < "$WORK/ws_events.jsonl" 2>/dev/null || echo 0)"
grep -o '"type": *"[a-z_]*"' "$WORK/ws_events.jsonl" 2>/dev/null | sort | uniq -c | sed 's/^/   ws: /' || true

echo "== [7.5/8] butler (P5a): LOW task + HIGH two-phase confirmation =="
printf '{"task":"立即巡检"}' > "$WORK/b1.json"
BUTLER=$(curl -s -X POST localhost:8123/api/v1/butler/task -H 'Content-Type: application/json' --data-binary @"$WORK/b1.json")
echo "   butler LOW: $(echo "$BUTLER" | head -c 160)"
printf '{"task":"清理 365 天前的日志"}' > "$WORK/b2.json"
PEND=$(curl -s -X POST localhost:8123/api/v1/butler/task -H 'Content-Type: application/json' --data-binary @"$WORK/b2.json")
TOKEN=$(echo "$PEND" | python -c "import sys,json;print(json.load(sys.stdin)['data'].get('token',''))")
echo "   butler HIGH staged: token=$TOKEN"
printf '{"task":"清理 365 天前的日志","confirm_token":"%s"}' "$TOKEN" > "$WORK/b3.json"
CONF=$(curl -s -X POST localhost:8123/api/v1/butler/task -H 'Content-Type: application/json' --data-binary @"$WORK/b3.json")
echo "   butler confirmed: $(echo "$CONF" | head -c 160)"
AUDIT_N=$(curl -s localhost:8123/api/v1/butler/audit | python -c "import sys,json;print(json.load(sys.stdin)['data']['count'])")
echo "   butler audit rows: $AUDIT_N"
echo "   knowledge-source demo (vector_store import):"
uv run python - << 'EOF'
import json, sqlite3, tempfile, urllib.request
from pathlib import Path
db = Path(tempfile.gettempdir()) / "kb-demo.vecdb"
conn = sqlite3.connect(db)
conn.execute("DROP TABLE IF EXISTS docs")
conn.execute("CREATE TABLE docs (id INTEGER PRIMARY KEY, title TEXT, content TEXT)")
conn.execute("INSERT INTO docs VALUES (1, '外部维保知识', '呼吸机管路每周检查密封圈并记录。')")
conn.commit(); conn.close()
BASE = "http://127.0.0.1:8123/api/v1"
def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req))
try:
    call("/knowledge-sources", {"name": "demo-ext", "type": "vector_store", "url": str(db)})
except urllib.error.HTTPError as e:
    if e.code != 409:
        raise
items = call("/knowledge-sources")["data"]["items"]
sid = next(s["id"] for s in items if s["name"] == "demo-ext")
r = call(f"/knowledge-sources/{sid}/sync", {})["data"]
print("   ks sync:", {k: r.get(k) for k in ("ok", "added")})
EOF

echo "== [8/8] golden evaluation suite (30 scenarios) =="
uv run python tests/eval/run_eval.py --fake 2>&1 | tail -8

echo
echo "============================================================"
echo "EXPERIMENT DONE — artifacts in $WORK"
echo "============================================================"
