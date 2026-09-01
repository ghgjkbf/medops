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
                                           │ streamable-http
┌─ core/ 业务后端 FastAPI ──┴───────────────────────────────┐
│ 秘书Agent(问答) · 管理Agent(巡检+诊断) · 日志分析管道       │
│ 预警引擎 · 台账/工单/周期提醒 · MCP Client                 │
└──────────────┬────────────────────────────────────────────┘
         REST + WebSocket
┌─ web/ Vue3 管理后台 ─────┐
│ 设备大盘/对话/预警/工单   │
└──────────────────────────┘
```

(Full architecture diagram and docs: placeholder — to be completed after P0. / 完整架构图与文档占位，P0 完成后补齐。)

## Dev Quickstart / 开发快速开始

> Placeholder — will be completed after P0 (repo scaffold + CI). / 占位——P0（仓库脚手架 + CI）完成后补齐。

```bash
# Python >= 3.11, < 3.13; requires uv
uv sync
uv run ruff check .
uv run pytest
```

## License

MIT © 2026 medops contributors
