# Contributing to medops / 贡献指南

Thanks for your interest in contributing! / 感谢关注与贡献！

## ⚠️ Disclaimer / 免责声明

**All device data in this project is SIMULATED. medops never connects to real
medical devices and must not be used for clinical decision-making.**

**本项目全部设备数据均为模拟生成。medops 不接入任何真实医疗设备，
严禁用于临床决策。**

## Dev setup / 开发环境

```bash
git clone <repo> && cd medops
uv sync                # Python workspace (core / common / sim / engine / mcp_servers)
cd web && npm install  # frontend
```

Local PostgreSQL is expected on port 55432 — see `deploy/README-pg.md`.

## Architecture in 60 seconds / 架构速览

- **Device layer**: one simulator process + one MCP server (streamable-http)
  per device; fault scenarios in `devices/engine/scenarios/`
- **Smart layer**: secretary agent (Q&A with tool trace) + inspector agent
  (scheduled inspection -> threshold rules -> LLM attribution -> alerts ->
  work orders); LLM fallback chain
- **Management layer**: FastAPI resource API + WebSocket, Vue3 SPA in `web/`

## Adding a new device / 接入新设备

The community growth path — one device = one simulator + one MCP server:

1. Create a scenario YAML in `devices/engine/scenarios/` (baseline metrics +
   fault drifts)
2. Create an MCP server package under `mcp_servers/<device>/` exposing its
   detection tools (`from mcp_fw import MedopsMCPServer`)
3. Register it in the `mcp_server` table (or `deploy/docker-compose.yml`)
4. Add threshold rules and at least one golden evaluation scenario
5. `uv run pytest` green + `ruff check` clean

## Rules / 规则

- **TDD**: tests first, then implementation; every PR keeps the suite green
- **Style**: `uv run ruff check .` must pass (CI enforces)
- **Commits**: `type(scope): description`
- **Frontend**: `npm run build` (type-check included) must pass
- Keep the work-order state machine single-sourced in
  `common/src/medops_common/constants.py` — do not fork it

## PR process / PR 流程

1. Fork / branch from `main`
2. Small, focused PRs with a clear description
3. CI must be green (ruff + pytest + coverage gate + web build)
4. One maintainer review minimum
