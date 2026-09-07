# medops

**EN** · An open-source reference implementation of an AI Agent management system for medical device operations: device capabilities are exposed via MCP, and dual agents (secretary Q&A + automated inspection) close the loop from detection and diagnosis to work orders.

**中文** · 面向医疗设备运维场景的开源 AI Agent 管理系统参考实现——设备能力通过 MCP 标准化接入，双 Agent（秘书问答 + 自动巡检）完成从检测、诊断到工单的闭环。

> ⚠️ **Disclaimer / 免责声明**: All device data is fully simulated. This project does NOT connect to real medical devices and must NOT be used for clinical decision-making.  
> 全部设备数据为模拟生成，不接入真实医疗设备，不用于临床决策。

## Architecture / 架构

```
┌─ devices/ 模拟设备层 ─────┐    ┌─ mcp_servers/ 设备能力接入层 ─┐
│ CT / DR / 呼吸机 / 心电    │───▶│ ct / dr / ventilator / ecg   │
│ 模拟器 + 故障剧本引擎      │    │ + device-status  (MCP Server)│
└──────────────────────────┘    └──────────┬───────────────────┘
                    本地TCP/文件交互        │ streamable-http
┌─ core/ 业务后端 FastAPI ──┴───────────────────────────────┐
│ 秘书Agent(问答) · 管理Agent(定时巡检+诊断) · 日志分析管道   │
│ 预警引擎 · 台账/工单/周期提醒 · MCP Client                 │
│ 内置工具: system(MCP健康/重启) · report(巡检报告)          │
└──────────────┬────────────────────────────────────────────┘
         REST + WebSocket        ┌─ infra ─────────────────┐
┌─ web/ Vue3 管理后台 ─────┐     │ PostgreSQL · Redis      │
│ 设备大盘/对话/预警/工单   │     │ LLM: API → Ollama 兜底  │
└──────────────────────────┘     └─────────────────────────┘
```

设计文档 / Design doc: [`docs/specs/2026-08-31-medops-design.md`](docs/specs/2026-08-31-medops-design.md)

**Current status / 当前状态**: management layer complete — **v0.1.0**. Web console (Vue3 + Element Plus + ECharts): dashboard with real-time alert stream, device ledger with metric trends, alert center (one-click → work order), work-order kanban with FSM-enforced transitions, maintenance plan/record management with due highlighting, MCP registry health, secretary-agent chat with tool-trace timeline. Backend: resource API (devices/alerts/work-orders/plans/records/logs/metrics + reports), WebSocket (`/ws/dashboard`, `/ws/chat`), maintenance reminders, external API onboarding, work-order FSM single-sourced in `medops_common`. 293 tests green, ruff clean, web build green. One-command demos: `bash scripts/demo_p1.sh` · `bash scripts/demo_p2.sh` · `bash scripts/experiment_p3.sh`. Plus: manual delete + bulk data cleanup (work orders / alerts / logs / metrics / devices / chat), and an in-app usage guide page. / 管理层完成——**v0.1.0**（P4a 新增删除与数据清理）。Web 控制台（Vue3 + Element Plus + ECharts）：总览大盘（实时告警流）、设备台账（指标趋势）、告警中心（一键转工单）、工单看板（状态机校验）、维保管理（到期高亮）、MCP 服务健康、智能问答（工具调用轨迹时间线）、数据清理、使用说明。后端：资源 API + 报告 + WebSocket + 周期提醒 + 外部 API 接入 + 删除/批量清理；工单状态机单点实现在 medops_common。293 个测试全绿，ruff/构建全绿。一键演示 `bash scripts/demo_p1.sh`（设备层）/ `bash scripts/demo_p2.sh`（智能层）/ `bash scripts/experiment_p3.sh`（全链路模拟实验）。

## ⚠️ Disclaimer / 免责声明

**All device data in this project is SIMULATED for education and research.
medops never connects to real medical devices and must not be used for
clinical decision-making. / 本项目全部设备数据均为模拟生成，仅用于教学与
研究。medops 不接入任何真实医疗设备，严禁用于临床决策。**

## User Guide / 使用说明

平台内置**「使用说明」页面**（控制台左侧导航），覆盖快速开始、功能导航、设备接入、巡检告警、工单流程、数据清理与常见问题。

- **一键启动（演示机）**：双击桌面「medops」快捷方式（或 `scripts/start.bat`）——自动拉起 PostgreSQL → 迁移 → 后端 → 浏览器；停止用「medops-停止」（`scripts/stop.bat`）。启动脚本按端口自动清理占用进程，可重复点击。
- **手动删除**：设备台账 / 告警中心 / 工单管理 / 维保管理 均提供删除按钮（二次确认）；删除设备会级联清理其告警、工单、维保计划/记录、日志与指标。
- **数据清理**：控制台「数据清理」页按时间范围（7/30/90 天前）批量清理告警/日志/指标，并可清空会话历史。

### Data cleanup API / 数据清理 API

```bash
curl -X DELETE 'localhost:8123/api/v1/work-orders/14'              # 删除工单（子引用置 NULL）
curl -X DELETE 'localhost:8123/api/v1/alerts/12'                   # 删除单条告警
curl -X DELETE 'localhost:8123/api/v1/alerts?level=critical'       # 按级别批量清理
curl -X DELETE 'localhost:8123/api/v1/alerts?before=2026-08-01T00:00:00'  # 按时间清理
curl -X DELETE 'localhost:8123/api/v1/logs?before=2026-08-01T00:00:00'    # 清理旧日志
curl -X DELETE 'localhost:8123/api/v1/metrics?device_id=ct-sim-01' # 按设备清理指标
curl -X DELETE 'localhost:8123/api/v1/devices/ct-sim-01'           # 删除设备 + 级联清理
curl -X DELETE 'localhost:8123/api/v1/chat-sessions'               # 清空会话历史
# 均返回 {"ok": true, "data": {"deleted": <N>}}；单条删除 404 时返回 "not found"
```

## External API Onboarding / 外部 API 接入 (P2.5)

Any third-party API (HIS, PACS, LLM gateways, ...) can be onboarded with its own URL + key; and any external system can call medops APIs with an API key. / 任意外部 API（HIS、PACS、LLM 网关等）按端点配置 URL+Key 接入；外部系统亦可持 Key 调用 medops 全部接口。

```bash
# Outbound: register an external API (bearer | header | none auth) / 出站注册
curl -X PUT localhost:8123/api/v1/endpoints -H 'Content-Type: application/json' -d '{
  "name": "his", "base_url": "https://his.hospital.local/api",
  "api_key": "xxx", "auth_type": "header", "api_header": "X-HIS-Key"}'

# Outbound: agents/backend call it via the relay / 经中继调用
curl -X POST localhost:8123/api/v1/endpoints/call -H 'Content-Type: application/json' \
  -d '{"endpoint": "his", "method": "GET", "path": "patients/123"}'

# LLM endpoints: register with kind=llm and they join the provider fallback chain
#（kind=llm 的端点自动并入 LLM 降级链，排在 env 配置的 provider 之后）

# Inbound: once ANY endpoint with a key exists, all /api/v1/* (except /health)
# require X-API-Key. No keys -> open mode (demo-friendly).
#（注册任一带 Key 端点后，全部 /api/v1/* 需带 X-API-Key；无 Key 时开放，便于演示）
curl localhost:8123/api/v1/agents/status -H "X-API-Key: your-key"
```

Endpoint list never echoes credentials (masked as `***`). / 端点列表永不回显凭据（掩码 `***`）。

## Dev Quickstart / 开发快速开始

### 0. Prerequisites / 前置条件

- **Python ≥ 3.11, < 3.13**
- **[uv](https://docs.astral.sh/uv/)**（`pip install uv` 或见官方安装脚本）

```bash
git clone <this-repo> && cd medops   # 或直接在本仓库根目录
uv sync                              # 创建 .venv 并安装 workspace 所有成员 + dev 依赖
```

之后所有命令统一通过 `uv run` 执行（无需手动激活虚拟环境）。

### 1. Run a device simulator / 起模拟器

```bash
uv run python -m medops_sim ct --scenario tube_overheat --outbox outbox
```

预期输出 / Expected output（关键行）：

```
[DICOM] wrote 3 files to outbox
[INFO] starting ct-sim-01 scenario=tube_overheat
[HB] ct-sim-01 seq=0 ts=2026-09-01T...
[METRIC] tube_temp 35.412
[METRIC] tube_exposure_count 0.000
...
[LOG] ... tube temperature above warning threshold (51 degC), check cooling
[SUMMARY] device=ct-sim-01 ticks=... metrics_emitted=... elapsed=...
```

输出行前缀说明：`[HB]` 心跳 · `[METRIC]` 指标 · `[LOG]` 设备日志 · `[DICOM]` 影像写入（仅 CT）· `[SUMMARY]` 退出汇总。Ctrl-C 可随时停止。

可用设备类型 / Device types：`ct` `ventilator` `dr` `ecg`。

### 2. Start the device-status MCP server / 起 device-status MCP server

```bash
uv run python -m mcp_device_status --transport http --port 8765
```

- 协议 / Transport: **streamable-http**，端点路径 `/mcp`（即 `http://127.0.0.1:8765/mcp`）。
- 工具 / Tools: `health_check` · `get_process_status` · `get_driver_info` · `get_file_integrity`（全部返回模拟数据）。

### 3. Call a tool / 调一个工具

新开一个终端，粘贴这段 10 行 Python / paste this in another terminal:

```bash
uv run python - <<'EOF'
import asyncio, json
from mcp.client import Client

async def main():
    async with Client("http://127.0.0.1:8765/mcp") as c:
        for tool, args in [("health_check", {}),
                           ("get_process_status", {"process_name": "python"})]:
            r = await c.call_tool(tool, args)
            data = r.structured_content or json.loads(r.content[0].text)
            print(f"{tool} -> {json.dumps(data, ensure_ascii=False)[:200]}")

asyncio.run(main())
EOF
```

预期输出 / Expected output（节选）：

```
health_check -> {"server": "medops-device-status", "status": "ok", "tools": ["get_driver_info", "get_file_integrity", "get_process_status", "health_check"], ...}
get_process_status -> {"process_name": "python", "found": true, "count": ..., ...}
```

### 4. Core API health check / core 健康检查

```bash
# 注意：workspace 根 package=false，uvicorn 需要 PYTHONPATH 指向 core/src
# Windows git-bash / POSIX:
PYTHONPATH=core/src uv run uvicorn medops_core.app:app --port 8123
# Windows PowerShell:
# $env:PYTHONPATH="core/src"; uv run uvicorn medops_core.app:app --port 8123
```

```bash
curl http://127.0.0.1:8123/api/v1/health
# {"status":"ok","service":"medops-core","mcp_servers":[]}
```

### 5. Run the tests / 运行测试

```bash
uv run ruff check .   # lint（配置在 ruff.toml）
uv run pytest         # testpaths=tests，293 个用例应全绿
```

### Built-in scenarios / 内置剧本

```bash
uv run python -m medops_sim ct --list-scenarios
```

| 剧本 / Scenario | 设备 | 时长 | 故事 |
|---|---|---|---|
| `tube_overheat` | ct | 600s | 球管冷却退化 → 温度爬升 → WARNING/ERROR → 自动热保护恢复 |
| `ct_pacs_disconnect` | ct | 420s | PACS 网络中断 → 影像本地排队 → 链路恢复重传（纯日志型剧本） |
| `o2_cell_drift` | ventilator | 600s | 氧电池漂移 → 氧浓度缓慢偏移 → 低氧报警 → 更换氧电池 |
| `ventilator_leak` | ventilator | 600s | 呼吸管路泄漏 → 气压/潮气量下降 → 密封恢复 |
| `disk_full` | dr | 600s | 磁盘写满 → 存储告警 → 清理恢复 |
| `dr_generator_overheat` | dr | 600s | 发生器冷却风扇故障 → kV 超差+温度爬升 → 换风扇恢复 |
| `dr_detector_cooling` | dr | 600s | 探测器制冷衰减 → 温度爬升 → 停机前告警 → 维护恢复 |
| `ecg_lead_off` | ecg | 360s | 导联脱落 → SNR 骤降 → 导联报警 → 重新贴片恢复 |
| `ecg_battery_low` | ecg | 600s | 电池老化 → 电压缓降 → 低电告警 → 接入电源 |

剧本为 YAML 文件，位于 `devices/engine/scenarios/`，也可 `--scenario path/to/your.yaml` 传入自定义剧本。

### One-shot demo / 一键演示

```bash
bash scripts/demo_p1.sh   # PG 迁移 → 模拟器×2 → MCP server×3 → 注册表 → 故障注入 → 建工单 → pytest
```

### Docker (optional / 可选)

`deploy/docker-compose.yml` 提供基础设施骨架（PostgreSQL + Redis），需要本机有 Docker；没有可直接跳过，上述全部命令均不依赖 Docker：

```bash
cd deploy && docker compose up -d
```

## License

MIT © 2026 medops contributors
