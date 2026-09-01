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

**Current status / 当前状态**: device layer fully runnable — 9 fault scenarios (3 per device: ct / dr / ventilator / ecg), 6 MCP servers (device-status / ct / dr / ventilator / ecg / maintenance-db on PostgreSQL), configuration downlink (`set_fault_scenario`), 11 core tables via Alembic migrations, MCP registry persisted to the `mcp_server` table. 152 tests green, ruff clean. One-command demo: `bash scripts/demo_p1.sh`. / 设备层全量可运行——9 个故障剧本（每设备 3 个）、6 个 MCP Server（maintenance-db 直连 PostgreSQL，工单状态机）、配置下发写路径（`set_fault_scenario`）、11 张核心表 Alembic 迁移、MCP 注册表持久化；152 个测试全绿；一键演示 `bash scripts/demo_p1.sh`。Web UI and agents are under active development. / Web 界面与 Agent 层开发中。

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
uv run pytest         # testpaths=tests，152 个用例应全绿
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
