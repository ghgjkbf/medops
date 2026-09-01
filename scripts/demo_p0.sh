#!/usr/bin/env bash
# scripts/demo_p0.sh — one-shot P0 demo: uv sync → simulator → MCP server → call tool → pytest.
# git-bash compatible. Exits non-zero on any failure; prints "P0 demo OK" on success.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd -W 2>/dev/null || pwd)"
export MSYS2_ARG_CONV_EXCL='*'   # keep /mcp and POSIX paths from being mangled for child processes

PIDS=()
OUTBOX=""
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  [ -n "$OUTBOX" ] && rm -rf "$OUTBOX" 2>/dev/null || true
}
trap cleanup EXIT

step() { echo; echo "==> $*"; }

step "1/6 uv sync"
uv sync

step "2/6 ct simulator (tube_overheat, 3s, outbox to temp dir)"
# NOTE: use a native Windows path for the outbox — the simulator runs under a
# native Python, for which MSYS /tmp is a *different* location than git-bash's.
OUTBOX_WIN="$LOCALAPPDATA/Temp/medops-p0-demo-$$"
mkdir -p "$OUTBOX_WIN"
OUTBOX="$(cd "$OUTBOX_WIN" && pwd)"   # MSYS view of the same dir
uv run python -m medops_sim ct --scenario tube_overheat --outbox "$OUTBOX_WIN" \
    > "$OUTBOX/sim.log" 2>&1 &
SIM_PID=$!
PIDS+=("$SIM_PID")
sleep 3
kill "$SIM_PID" 2>/dev/null || true
wait "$SIM_PID" 2>/dev/null || true
grep -q '^\[METRIC\]' "$OUTBOX/sim.log" && echo "simulator emitted metrics: $(grep -m1 '^\[METRIC\]' "$OUTBOX/sim.log")"
DCM_COUNT=$(find "$OUTBOX" -name '*.dcm' | wc -l)
if [ "$DCM_COUNT" -lt 1 ]; then
  echo "FAIL: no .dcm files in $OUTBOX" >&2; exit 1
fi
echo "DICOM ok: $DCM_COUNT .dcm file(s) in outbox"

step "3/6 device-status MCP server on :8765"
uv run python -m mcp_device_status --transport http --port 8765 \
    > "$OUTBOX/mcp.log" 2>&1 &
SRV_PID=$!
PIDS+=("$SRV_PID")
READY=0
for _ in $(seq 1 40); do
  # readiness = TCP connect succeeds (the endpoint 400s on a bare ping pre-session)
  if uv run python -c "import socket,sys; s=socket.create_connection(('127.0.0.1',8765),timeout=1); s.close()" 2>/dev/null; then
    READY=1; break
  fi
  kill -0 "$SRV_PID" 2>/dev/null || { echo "FAIL: MCP server died, log:"; cat "$OUTBOX/mcp.log"; exit 1; }
  sleep 0.5
done
[ "$READY" = 1 ] || { echo "FAIL: MCP server not ready on :8765" >&2; cat "$OUTBOX/mcp.log" >&2; exit 1; }
echo "MCP server ready at http://127.0.0.1:8765/mcp"

step "4/6 call health_check + get_process_status via MCP client"
uv run python - <<'PYEOF'
import asyncio, json
from mcp.client import Client

async def main():
    async with Client("http://127.0.0.1:8765/mcp") as c:
        for tool, args in [("health_check", {}),
                           ("get_process_status", {"process_name": "python"})]:
            r = await c.call_tool(tool, args)
            data = r.structured_content or json.loads(r.content[0].text)
            print(f"{tool} -> {json.dumps(data, ensure_ascii=False)[:200]}")
        assert not r.is_error

asyncio.run(main())
PYEOF

step "5/6 cleanup background processes"
cleanup
PIDS=()
echo "background processes stopped"

step "6/6 final gate: uv run pytest -q"
uv run pytest -q

echo
echo "P0 demo OK"
