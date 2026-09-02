# medops 智能层架构（P2）

> 智能层组件图 + 巡检/问答时序。设计依据 `docs/specs/2026-08-31-medops-design.md` §5/§7/§9/§10。

## 组件图

```
┌─ core/agents/ 智能层 ──────────────────────────────────────────┐
│ llm.py     LLMClient 降级链：DeepSeek→Qwen→Ollama→规则兜底      │
│            （FakeLLM 测试注入；MEDOPS_LLM_MODE=fake 离线演示）  │
│ base.py    BaseAgent：工具注册表 + 轨迹记录（ToolCall）         │
│ inspector  管理 Agent：定时巡检 → 阈值判定 → 归因 → Alert/工单  │
│ secretary  秘书 Agent：意图规则分类 → MCP 工具 → 回答+轨迹      │
│ scheduler  APScheduler 封装（lifespan 启停，幂等）              │
├─ core/log_pipeline/ ───────────────────────────────────────────┤
│ collector（offset 增量读日志）→ rules（关键字/级别命中）        │
│ → analyzer（按设备分组 LLM 归因，降级为规则摘要）→ Alert        │
├─ core/alerting.py ─────────────────────────────────────────────┤
│ 重复告警升级（WARNING→CRITICAL）+ 通知 sink（控制台/P3 WS）     │
└────────────────────────────────────────────────────────────────┘
        │ MCPRegistry（P1）— streamable-http 调 6 个 Server
        ▼
   mcp_servers/*（ct/dr/ventilator/ecg/maintenance-db/device-status）
```

阈值单一来源：`medops_common.thresholds`（P1 Server 与 P2 pipeline/inspector 共用）。

## 巡检链路时序（设计 §5.1）

```
APScheduler(60s)   InspectorAgent        MCPRegistry/Server      PostgreSQL
    │                  │                       │                    │
    ├─ run_inspection ─▶ 遍历 handles          │                    │
    │                  ├─── call_tool(检测类)──▶ 真实执行          │
    │                  │◀── metrics 快照 ──────┤                    │
    │                  │ 阈值判定（共享表）     │                    │
    │                  │ 异常 → LLM 归因（降级链，失败转规则摘要）   │
    │                  ├──────────────────────────────────────────▶ Alert 入库
    │                  │ CRITICAL → WorkOrder（签名幂等）────────▶ 工单入库
    │ ◀─ InspectionResult ─┤                     │                    │
```

动作分级落地：巡检只调 READ_ONLY 检测工具；`set_fault_scenario` 等
HIGH_RISK_WRITE 工具永远不在自动巡检里执行（仅演示/人工触发）。

## 问答链路时序（设计 §5.2）

```
用户 ──▶ POST /api/v1/chat ──▶ SecretaryAgent
                                  ├─ classify_intent（规则表，确定性路由）
                                  ├─ registry.call_tool（轨迹记录，失败也记）
                                  ├─ LLM 组织回答（FakeLLM 可注入）
                                  └─ chat_session/chat_message 落库
用户 ◀── {answer, trajectory[], provider_used} ──
```

## 评测（设计 §10）

`tests/eval/`：30 个黄金场景（状态 10 / 故障 12 / 台账 5 / 闲聊 3）。
离线模式（`run_eval.py --fake`）产出工具命中率/错误率/时延指标；
配置真实 key 后可跑 LLM-as-judge 评归因质量。

## 复现

```bash
# 前置：PG 运行（deploy/README-pg.md）
bash scripts/demo_p2.sh                          # MEDOPS_LLM_MODE=fake 默认
DEEPSEEK_API_KEY=sk-x bash scripts/demo_p2.sh    # 真实 LLM 路径
```
