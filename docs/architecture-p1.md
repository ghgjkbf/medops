# medops 设备层架构（P1）

> 设备层组件图 + 配置下发（写路径）时序。设计依据 `docs/specs/2026-08-31-medops-design.md` §3/§5.4。

## 组件图

```
 devices/ 模拟设备层                        mcp_servers/ 设备能力接入层
┌─────────────────────────┐   metrics_snapshot.json   ┌──────────────────────┐
│ medops-sim (ct/vent/…)  │ ─────────(原子写)───────▶ │ mcp-ct      :8801    │
│  DriftModel ×N 指标     │ ◀─────(控制文件轮询)────── │ mcp-dr      :8802    │
│  ScenarioEngine 剧本    │   control/<dev>_fault.json│ mcp-vent    :8803    │
│  FaultController        │                           │ mcp-ecg     :8804    │
│  Heartbeat/LogStream    │                           │ mcp-device  :8765    │
└─────────────────────────┘                           │ mcp-maint-db :8805 ──┼──▶ PostgreSQL
                                                      └──────────┬───────────┘    (medops 库)
                                              streamable-http /mcp
                                                      │
┌─ core/ FastAPI ─────────────────────────────────────┴──────────────┐
│ MCPRegistry（连接管理·健康轮询·超时降级）— 状态同步 mcp_server 表   │
└────────────────────────────────────────────────────────────────────┘
```

指标通道（P1）：**快照文件**。模拟器每 tick 原子写 `<outbox>/metrics_snapshot.json`
（tmp+rename，读侧永不读到半截文件）；MCP Server 只读。TCP 通道留 P2。

控制通道（P1）：**控制文件**。MCP 工具 `set_fault_scenario` 原子写
`<outbox>/control/<device>_fault.json`；模拟器每 tick 轮询 mtime，变化即应用。

## 配置下发时序（写路径，演示核心）

```
Agent(P2)/演示脚本      mcp-ct Server          模拟器(medops-sim ct)        PostgreSQL
    │                        │                        │                        │
    │ call_tool(             │                        │                        │
    │  "set_fault_scenario", │                        │                        │
    │  fault={tube_temp,+30})│                        │                        │
    │───────────────────────▶│ 原子写 control/ct_fault.json                    │
    │                        │───────────────────────────────┐          │
    │ ◀── accepted=True ─────│                        下个 tick 轮询到 mtime 变化
    │   (high_risk_write)    │                        inject_fault(+30)        │
    │                        │                        ▼                        │
    │ call_tool("get_tube_stats")      metrics_snapshot.json: tube_temp 35→66
    │───────────────────────▶│◀───────(读快照)────────│                        │
    │ ◀── status=error ──────│                        │                        │
    │                        │                        │                        │
    │ call_tool("create_work_order", {device_id: ct-sim-01, dedupe_key})      │
    │───────────────────────────────────────────────────────────────────────▶│
    │ ◀── work_order(id, pending, low_risk_write) ────────────────────────────│
```

动作分级（设计 §5.1）在工具元数据中体现：读工具 READ_ONLY、建单 LOW_RISK_WRITE、
`set_fault_scenario`/工单跃迁 HIGH_RISK_WRITE——P2 管理 Agent 将据此要求人工确认。

## 复现

```bash
# 前置：PG 运行（deploy/README-pg.md）
bash scripts/demo_p1.sh
```
