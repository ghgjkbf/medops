# 基于 MCP + AI 的医疗器械运维智能管理系统 · 设计文档

- 日期：2026-08-31
- 状态：v1（已经三轮评审确认）
- 仓库：`C:\Users\Administrator\Desktop\project`

---

## 1. 定位

**开源版（一句话）**：面向医疗设备运维场景的开源 AI Agent 管理系统参考实现——设备能力通过 MCP 标准化接入，双 Agent（秘书问答 + 自动巡检）完成从检测、诊断到工单的闭环。

**表述（用于对外介绍材料）**：基于 MCP 协议与多 Agent 协作的医疗器械智能运维管理系统的设计与实现。

**差异化**：GitHub 上 AI 运维开源项目集中于 IT 基础设施（Wuhr-AI-ops、itops-agent-platform、SxDevOps 等），医疗方向 MCP 项目集中于医学知识问答（medical-mcp）。「医疗设备 OT 运维 × MCP × 双 Agent」为空白交叉点。

**功能范围**：
1. 本地医疗器械环境自动检测（CT、DR、呼吸机、心电设备的文件、驱动/进程、运行状态）
2. AI Agent 智能故障诊断（秘书 Agent 问答 + 管理 Agent 自动巡检诊断）
3. 设备日志 AI 分析、异常预警
4. 设备维保台账、维修记录、周期提醒
5. Web 可视化管理后台

## 2. 关键决策记录

| # | 决策 | 理由 |
|---|------|------|
| D1 | 全模拟设备路线（自研模拟器，不接真实设备） | 风险最低、演示可控；检测逻辑真实可用 |
| D2 | Python 技术栈（FastAPI + LangGraph + MCP Python SDK） | MCP 生态最顺、双 Agent 编排成熟、日志分析库丰富 |
| D3 | LLM：国产 API 为主（DeepSeek/Qwen）+ Ollama 本地兜底 | 便宜、国内直连；断网演示有保障 |
| D4 | MCP 部署形态选「拟真分布式」：每台模拟设备 = 独立进程 = 独立 MCP Server（streamable-http） | 还原真实医院拓扑（CT 在放射科、呼吸机在 ICU）；可演示拔设备离线告警；适合 docker compose 一键拉起；方案对比论证见下 |
| D5 | 模拟器与 MCP Server 分离（medops-sim / medops-engine 独立包） | 「接入新设备只需写一个 MCP Server + 一个模拟器」是开源生长点；故障注入引擎独立为核心模块 |
| D6 | 主库 PostgreSQL；`device_metric` 按 TimescaleDB hypertable 规范设计（兼容不实做） | 时序库升级路径表述诚实成立 |
| D7 | DICOM 只做文件级（共享目录写入 .dcm + 元数据/完整性检测），不做 DICOM 网络协议 | 工作量边界，见「范围边界」一节 |
| D8 | system / report 为 core 内置工具（builtin），不走 MCP | 它们操作 MCP 层本身，走 MCP 会循环依赖 |
| D9 | MIT License；中英双语 README（英文优先）；发布 v0.1.0 对齐 P3 | 开源规范与节奏 |

**方案对比（D4）**：
- A 一体化单机（stdio MCP）：开发最快，但拟真度低、无 compose 价值 → 弃
- B 拟真分布式（streamable-http）：拓扑还原、可演示离线、社区可扩展 → **采用**
- C 纯 function calling：最省事但违背选题定位 → 仅作为架构对照方案

## 3. 总体架构

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

## 4. Monorepo 模块划分

```
medops/
├─ common/             # 公共包：通用常量、工具函数、schema
├─ devices/            # 设备模拟器（medops-sim）
│  ├─ ct/ dr/ ventilator/ ecg/
│  └─ engine/          # 故障剧本引擎（medops-engine，独立包）
│                      #   剧本编排 + 指标漂移模型 + DICOM 文件写入器
├─ mcp_servers/        # 每类设备一个 MCP Server
│  └─ framework/       # Server 模板基类 ← 社区扩展点
├─ core/               # FastAPI 业务后端
│  ├─ agents/          # secretary(问答) / inspector(巡检) / llm.py(多provider降级链)
│  ├─ mcp_client/      # 连接管理、工具注册、设备健康检查
│  ├─ log_pipeline/    # 采集(tail) → 规则引擎 → LLM归因 → 预警
│  ├─ api/  models/    # REST + WebSocket 路由 / ORM（SQLAlchemy + PostgreSQL）
├─ web/                # Vue3 + Element Plus + ECharts
├─ deploy/             # docker-compose.yml + .env.example
├─ docs/               # 架构图、接入教程
└─ tests/              # pytest 单测 + 集成冒烟 + Agent 评测集
```

## 5. 数据流（四条主链路）

1. **巡检链路**：定时器 → 管理 Agent 选择检测工具（MCP）→ 结构化结果 → 规则判定 → 异常则 LLM 归因 → 写预警 + 自动创建工单 → WebSocket 推前端。
   - **自动处置分支**：动作分级——只读查询（自动）/ 低风险写（重置模拟模块、清理临时文件：Agent 自动执行并落审计日志）/ 高风险写（重启服务、修改参数：必须生成工单等人工确认）。
2. **问答链路**：用户提问 → 秘书 Agent 判断意图 → 调 MCP 工具查状态/日志/台账 → 结合知识库 → 回答；前端展示完整工具调用轨迹（核心演示画面）。
3. **日志链路**：模拟器写日志文件 → tail 采集 → 规则引擎（阈值/关键字，毫秒级）→ 事件入库 → LLM 低频批量归因 → 预警分级。规则引擎负责「快」，LLM 负责「懂」，成本由此可控。
4. **配置下发链路**：管理 Agent 可通过 MCP 修改模拟器故障参数（故障剧本触发），一键造故障——核心演示能力，同时补全 MCP 写路径（带副作用的控制指令）。

## 6. 数据库（PostgreSQL，11 张核心表）

| 表 | 说明 |
|---|---|
| device | 设备台账：类型/型号/科室/状态/注册的 MCP Server |
| device_metric | 时序指标；按 TimescaleDB hypertable 规范设计（兼容不实做，见 D6） |
| device_log | 设备日志原文与结构化字段 |
| alert | 预警：级别/归因（LLM 或规则降级摘要）/关联工单 |
| work_order | 工单状态机：待接单→处理中→待验证→关闭 |
| maintenance_plan | 周期维保计划 |
| maintenance_record | 维保/维修记录 |
| chat_session / chat_message | 秘书 Agent 会话与消息（含工具调用轨迹） |
| knowledge_doc | 知识库文档（配 embedding） |
| mcp_server | MCP 服务注册表：名称/传输协议/端点/健康度/工具列表(JSON)/最后心跳 |

## 7. Agent 工具清单

**MCP 工具（按 Server 分组）**：

| MCP Server | 工具 |
|---|---|
| device-status | `get_process_status` / `get_driver_info` / `get_file_integrity` |
| ct | `check_dicom_dir` / `get_tube_stats`(温度/曝光数) / `check_pacs_connectivity` |
| dr | `check_generator_status` / `get_detector_temp` |
| ventilator | `get_realtime_params`(潮气量/氧浓度/气压) / `run_self_test` |
| ecg | `get_waveform_quality` / `check_export_files` |
| maintenance-db | `query_devices` / `get_maintenance_due` / `create_work_order` / `update_work_order` / `add_repair_record` / `query_alerts` |
| 设备模拟器(各设备) | `set_fault_scenario`（配置下发，D4/数据流4） |

**core 内置工具（builtin，见 D8）**：
- `system` 工具集：MCP 服务健康检查、重启、连通性检测
- `report` 工具：一键生成巡检报告

## 8. API 骨架

- FastAPI，前缀 `/api/v1`，统一响应包络 + Pydantic schema
- 资源路由：`devices` / `metrics` / `logs` / `alerts` / `work-orders` / `maintenance-plans` / `maintenance-records` / `mcp-servers` / `reports` / `chat`
- WebSocket：`/ws/dashboard`（状态流 + 预警推送）、`/ws/chat/{session_id}`（流式回答 + 工具调用轨迹事件）

## 9. 错误处理与降级（四层）

1. **设备层**：模拟器崩溃/心跳超时 → MCP Server 上报离线 → 预警（本身是被检测的「故障」之一）
2. **MCP 层**：调用超时/连接失败 → Agent 标记该设备不可用，巡检继续其余设备，不阻塞
3. **LLM 层**：降级链 DeepSeek → Qwen（备用 API） → Ollama 本地 → 纯规则引擎结论（预警永不因 LLM 故障丢失，仅「归因」字段降级为规则摘要）
4. **前端**：全局错误码 + WebSocket 断线自动重连

## 10. 测试策略（测试数据兼作实验数据）

- **单元**（pytest）：规则引擎、剧本引擎、工具参数校验；核心模块覆盖率 ≥80%
- **集成**：docker compose 拉起模拟器 + MCP + core 全链路冒烟
- **Agent 评测集**：30–50 个黄金场景（故障注入 → 期望工具调用序列 + 期望诊断结论）；脚本断言工具选择正确性，LLM-as-judge 评归因质量 → 产出「诊断准确率 / 工具命中率」等指标
- **CI**：GitHub Actions（ruff + pytest + web build），PR 必须绿

## 11. 开源规范

- MIT License；中英双语 README（英文优先：一键 `docker compose up` + 演示 GIF + 架构图）
- CONTRIBUTING + 「接入新设备」教程（社区生长点：新设备 = 一个 MCP Server + 一个模拟器）
- issue/PR 模板
- **免责声明（必须）**：全部设备数据为模拟生成，不接入真实医疗设备，不用于临床决策
- v0.1.0 对齐 P3（2027.7）；可选发布到 MCP Registry 增加曝光
- 「离线演示」预案：Ollama 本地模型 + 全部进程本机运行（应对现场网络不稳定）

## 12. 里程碑（阶段末必有可运行版本）

| 阶段 | 时间 | 交付物 |
|---|---|---|
| P0 设计与脚手架 | 2026.9–10 | 设计文档定稿、repo + CI、模拟器引擎原型 |
| P1 设备层 | 2026.11–2027.1 | 4 类模拟器 + 故障剧本库、6 个 MCP Server（含 maintenance-db） |
| P2 智能层 | 2027.2–4 | 双 Agent、日志管道、预警引擎 |
| P3 管理层 | 2027.5–7 | Web 后台、台账/工单/提醒闭环 → **v0.1.0 发布** |
| P4 打磨 | 2027.8–10 | 双语文档/Ollama 兜底/演示剧本/社区模板，v0.2 |
| P5 实验与打磨 | 2027.11–2028.1 | 功能+性能测试、LLM 方案对比实验、文档完善 |
| P6 收尾 | 2028.2–5 | 长期维护、社区运营、版本迭代 |

## 13. 风险与对策

| 风险 | 对策 |
|---|---|
| 功能膨胀（周期长达 21 个月） | 里程碑制，每阶段末可运行；v0.1 后新功能进 backlog 不进承诺 |
| 演示现场断网/LLM 不稳定 | Ollama 本地兜底 + 离线演练剧本；规则引擎保证预警链路不依赖 LLM |
| 演示故障不可复现 | 故障剧本引擎（medops-engine）+ 配置下发一键造故障 |
| 「模拟数据说服力」质疑 | 检测逻辑真实（进程/文件/驱动级检测）；明确研究范围（D7）；三方案对比论证架构 |
| LLM 成本失控 | 规则引擎前置过滤 + LLM 低频批量归因；评测集脚本化，不烧 token 反复人工测 |

## 14. 范围边界

- 不接入真实医疗设备，全部数据由模拟器生成；系统定位为「参考实现」而非可临床部署产品
- DICOM 仅文件级处理，不实现 DICOM 网络协议（C-STORE 等）
- 不做 HL7/FHIR 集成、不做多租户、不做移动端
- LLM 输出仅作运维辅助建议，不构成任何医疗诊断
