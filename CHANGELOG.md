# Changelog / 更新日志

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/) + SemVer.

## [Unreleased]

## [0.1.0] — 2026-09-07

Management layer complete; first public release.

### Added
- **Web console (Vue3 + Element Plus + ECharts)**: dashboard with live alert
  stream, device ledger with metric trends, alert center with one-click
  work-order conversion, work-order kanban (state machine enforced),
  maintenance plan/record management with due highlighting, MCP registry
  health view, secretary-agent chat with tool-trace timeline
- **Resource API**: devices / alerts / work-orders / maintenance-plans /
  maintenance-records / logs / metrics (pagination, filters, unified
  envelope); work-order FSM shared by REST and MCP (single source in
  medops_common)
- **Report API**: windowed Markdown report (devices/alerts/work-orders),
  optional LLM polish with silent template fallback
- **WebSocket**: `/ws/dashboard` (status + real-time alert push through the
  AlertingEngine sink chain) and `/ws/chat/{session_id}` (tool-trace +
  answer events)
- **Maintenance reminders**: hourly scan of due plans -> warning alert,
  cycle rolled forward (idempotent); `alert.kind` column
- **External API onboarding (P2.5)**: register any API with per-endpoint
  URL + key (bearer/custom-header), outbound relay `/endpoints/call`,
  inbound X-API-Key auth, kind=llm endpoints join the LLM fallback chain
- **Smart layer (P2)**: dual agents, LLM fallback chain, log pipeline,
  alert escalation, 30-scenario golden evaluation suite (100% offline tool
  hit rate)
- **Device layer (P1)**: 4 simulator families, 9 fault scenarios, 6 MCP
  servers, configuration downlink (`set_fault_scenario`)

### Notes
- All device data is simulated; never connect real medical devices
- PostgreSQL 16 on port 55432 for local dev (`deploy/README-pg.md`)
