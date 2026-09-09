# Changelog / 更新日志

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/) + SemVer.

## [Unreleased]

## [0.5.0-p6c] — 2026-09-09

Builtin plugin framework + five skills.

### Added
- Plugin registry: manifest-driven skills, enable state persisted (`plugin`
  table), risk levels, **env-gated risky skills** (console_actor /
  doc_searcher require MEDOPS_PLUGIN_CONSOLE / MEDOPS_PLUGIN_SEARCH and are
  blocked even when enabled in the UI without the gate)
- Skills: prompt_builder (spec→prompt, LLM polish hook), code_guard
  (template codegen + compile/structural checks, LLM review hook),
  tool_finder (keyword ranking over MCP tools + butler ops),
  console_actor (simulated device console macros → device MCP tools),
  doc_searcher (offline KB first, optional online endpoint)
- REST /api/v1/plugins (list / enable / run) + 插件 panel in the web console
- LLM enhancement hooks run only when a real provider is attached

## [0.5.0-p6b] — 2026-09-09

Secretary requirement gathering.

### Added
- RequirementFSM: vague/requesty input → structured question bank (≤3 rounds)
  → complete prompt spec (role/context/action/output) → targeted inspection
  (`inspector.inspect_with_spec`, device-type filter) → summary
- LLM question phrasing (async-safe, deterministic bank fallback)
- Concrete intents bypass the FSM (golden scenarios unchanged)

## [0.5.0-p6a] — 2026-09-09

Fault triage & remediation (three channels).

### Added
- `remediation.py`: rule-first classification + optional LLM check
  (`llm_verified`); platform_sw auto-heals through butler ops (low auto,
  high consent) with verify + escalation; device_sw (firmware/config/agent)
  **always asks the user first**; hardware → remediation package
  (cause/steps/risk/verification)
- Device-system toolset: get_device_info / upgrade_firmware / apply_config /
  restart_device_agent (shared via `mcp_fw.device_system`, sim-side state +
  FaultController fault/repair commands)
- Alert.meta migration 0007; WS alert payload includes meta
- REST: GET /alerts/{id}/remediation + POST agree; AlertsView disposition
  column + consent dialog

## [0.4.0-p5c] — 2026-09-08

Butler agent + three-tier retrieval + external knowledge sources.

### Added
- **Butler agent** (the inspector doubles as the management agent):
  deterministic operation intents covering work-order FSM transitions and
  creation, deletes, bulk cleanup, MCP register/remove, endpoint
  toggle/delete, triggering an inspection, platform report/status;
  HIGH-risk actions default to a two-phase dry-run → confirmation-token
  flow (10 min TTL), `MEDOPS_BUTLER_AUTO=1` for unattended demos; every
  execution lands in the `butler_audit` table; REST `POST
  /api/v1/butler/task` + `GET /api/v1/butler/audit`; secretary delegates
  operation intents to the butler with the trajectory merged into the
  chat timeline
- **Three-tier retrieval** behind `retrieve(query, top_k, backend)` with
  `MEDOPS_KB_BACKEND=keyword|vector|legacy` (default keyword):
  keyword = jieba terms + bm25s BM25 (new default, fully offline);
  vector = fastembed BGE-small-zh-v1.5 (optional `[vector]` extra) +
  sqlite-vec (`deploy/knowledge.vecdb`, rebuildable from PG) with
  automatic keyword fallback; legacy 2-gram kept
- **External knowledge sources**: `knowledge_source` table binding
  web | rss | vector_store; ingestion pipeline with SSRF guard
  (private-address rejection, `MEDOPS_KB_ALLOW_HOSTS` allowlist, 1 MB
  cap), HTML tag stripping, ~800-char chunking, sha256 dedupe; REST
  CRUD + `POST /{id}/sync`; scheduled sync via lifespan task (manual
  default); butler tools `sync_knowledge_source` / `list_knowledge_sources`
- Web knowledge page: retrieval-backend badge + source
  binding/sync/delete panel

### Fixed
- device MCP servers are root workspace dependencies now — a plain
  `uv sync` pruned them from the venv and broke every demo
- experiment script: leftover process sweep + connect-retry loop
  (TCP-ready ≠ MCP-protocol-ready), MSYS kill replaced by port/cmdline
  PowerShell sweep, UTF-8 file bodies for Chinese curl payloads
- registry `last_error` now unwraps ExceptionGroup leaves

## [0.3.0-p4c] — 2026-09-07

Knowledge base + agent fault tools; ops polish for demo machines.

### Added
- **Knowledge base**: deterministic Chinese 2-gram scored retrieval
  (`medops_core.knowledge`); REST (`GET/POST/DELETE /api/v1/knowledge`,
  `POST /api/v1/knowledge/import` multipart ≤512KB); 11 builtin
  cause/handling docs (9 fault scenarios + 2 workflow docs) seeded
  idempotently at startup; web「知识库」page (search / import / delete)
- **Agent tools**: secretary `search_knowledge` + `get_fault_report`
  (per-device markdown: alerts / warn-error logs / work orders /
  maintenance records, scope resolved from the question); inspection
  attribution enriched with the top knowledge hit
- **Hidden startup**: `start.bat` launches PostgreSQL + backend with no
  console windows (PowerShell wrappers); `stop.bat` switches to
  port-based kill; portable-PG path overridable via `MEDOPS_PGBIN`

### Changed
- `DELETE /api/v1/endpoints/{name}` now removes the endpoint row
  (previously disable-only); deleting the last keyed endpoint reverts
  inbound auth to open mode
- golden evaluation routing: 怎么处理 / 什么原因 questions route to the
  knowledge base instead of device status tools

### Fixed
- `.bat` scripts restored to CRLF (LF broke cmd's multi-line `if` blocks)

## [0.2.0-p4b] — 2026-09-07

Frontend onboarding for external APIs + runtime MCP configuration.

### Added
- MCP quick-config API: `GET/POST/DELETE /api/v1/mcp-servers` (register /
  remove at runtime; live registry + `mcp_server` table; offline
  registration allowed), `MCPRegistry.remove` + `ServerHandle.close`
- Web「MCP 服务」page: register/remove form, tool list, state mapping fix
- Web「API 接入」page: external endpoint list, create (bearer/header,
  kind=llm joins the fallback chain), enable/disable switch, real delete,
  local X-API-Key unlock for armed inbound auth
- `PATCH /api/v1/endpoints/{name}` enable toggle

## [0.2.0-p4a] — 2026-09-07

Manual delete + data cleanup + usage docs.

### Added
- Delete / bulk-cleanup API: work orders (children detached), alerts
  (single + bulk by device/level/age), logs & metrics (bulk by age),
  maintenance plans/records, devices (cascade-cleans six child tables),
  chat history clear; `DELETE` responses report `{deleted: N}`
- Web delete buttons on work orders / alerts / devices / maintenance
  (confirm dialogs; device delete warns cascade);「数据清理」bulk-cleanup
  page; in-app usage-guide page (使用说明)
- One-command full-chain simulation experiment `scripts/experiment_p3.sh`
  (fault injection → inspection → alert → work order → WS → chat →
  report → 30-scenario golden eval)
- Quick-start launcher `scripts/start.bat` / desktop shortcut

### Fixed
- WebSocket alert events no longer lost (fire-and-forget `create_task`
  now holds strong refs); status broadcaster survives per-cycle errors;
  backend restart kills by port instead of window title

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
