<script setup lang="ts">
// 使用说明 / 教程：结构化的快速上手与功能导览。
const sections = [
  {
    title: '快速开始',
    body: [
      '双击桌面「medops」快捷方式即可一键启动：PostgreSQL → 后端 → 浏览器控制台。',
      '启动完成后浏览器自动打开 http://127.0.0.1:8123，无需手动配置。',
      '停止服务请双击桌面「medops-停止」。日志见 deploy/api.log。',
    ],
  },
  {
    title: '功能导航',
    body: [
      '总览 Dashboard：设备健康总览与 MCP 服务实时状态。',
      '设备台账：设备列表与实时指标曲线（管球温度 / 探测器温度 / 氧浓度）。',
      '告警中心：预警列表，支持按级别筛选、转工单、删除与清空。',
      '工单管理：看板式工单状态机（待接单 → 处理中 → 待验证 → 已关闭）。',
      '维保管理：周期维保计划与维保/维修记录。',
      'MCP 服务：设备接入服务（MCP Server）的注册状态与工具列表。',
      '智能问答：与运维秘书对话，自动调用设备工具并展示调用轨迹。',
      '数据清理：批量清理告警/日志/指标等冗余数据。',
    ],
  },
  {
    title: '设备接入（MCP）',
    body: [
      '每台模拟设备是一个独立的 MCP Server（streamable-http），通过配置注册到平台。',
      'MCP 服务页可查看每个服务的健康状态与工具列表；后端启动时自动从数据库加载注册表并连接。',
      '运行 scripts/experiment_p3.sh 可体验完整链路：故障注入 → 巡检 → 告警 → 工单 → 实时推送 → 问答 → 报告。',
    ],
  },
  {
    title: '巡检与告警',
    body: [
      '平台内置巡检 Agent，按固定周期调用各设备工具采集指标，超阈值即产生告警。',
      'critical 级别告警会自动创建关联工单（按去重键合并）。',
      '巡检结果、告警、工单均通过 WebSocket 实时推送到控制台。',
    ],
  },
  {
    title: '工单流程',
    body: [
      '工单状态机：pending（待接单）→ in_progress（处理中）→ awaiting_verification（待验证）→ closed（已关闭）。',
      '非法状态迁移（如 pending 直接跳 closed）会被拒绝并返回 409。',
      '删除工单会解除其与告警/维保记录的绑定，告警与记录本身保留。',
    ],
  },
  {
    title: '数据清理',
    body: [
      '告警中心：单条删除或一键清空全部告警。',
      '数据清理页：按时间范围（7/30/90 天前）批量清理告警/日志/指标，清空会话历史。',
      '设备台账：删除设备会级联删除其告警、工单、维保计划/记录、日志与指标。',
      '删除操作均不可恢复，请谨慎确认。',
    ],
  },
  {
    title: '常见问题',
    body: [
      'Q：告警每 20 秒重复推送？A：告警当前无冷却去重，可在告警中心手动清理，冷却机制在后续版本提供。',
      'Q：智能问答没有调用工具？A：默认 fake 模式返回模拟回答；接入真实 LLM（DeepSeek/Qwen）后启用真实工具调用。',
      'Q：后端启动失败？A：确认 5432/8123 端口未被占用，查看 deploy/api.log；启动脚本会按端口自动清理占用进程。',
    ],
  },
]

const flowSteps = ['待接单 pending', '处理中 in_progress', '待验证 awaiting_verification', '已关闭 closed']
</script>

<template>
  <div class="help">
    <el-card shadow="never" class="hero">
      <h2 class="h">medops 医疗器械运维平台 · 使用说明</h2>
      <p class="sub">一个基于 MCP + 大模型的设备运维演示系统：设备接入、智能巡检、告警、工单、问答一站式。</p>
    </el-card>

    <el-card shadow="never" class="mt12">
      <template #header>工单流转（状态机）</template>
      <el-steps :active="4" align-center finish-status="success">
        <el-step v-for="s in flowSteps" :key="s" :title="s.split(' ')[1]" :description="s.split(' ')[0]" />
      </el-steps>
    </el-card>

    <el-card shadow="never" class="mt12">
      <el-collapse accordion>
        <el-collapse-item v-for="s in sections" :key="s.title" :name="s.title">
          <template #title><b>{{ s.title }}</b></template>
          <ul class="list">
            <li v-for="(line, i) in s.body" :key="i">{{ line }}</li>
          </ul>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<style scoped>
.h { margin: 0; font-size: 20px; }
.sub { color: #909399; margin: 8px 0 0; font-size: 13px; }
.mt12 { margin-top: 12px; }
.list { padding-left: 18px; margin: 0; }
.list li { margin: 6px 0; font-size: 13px; line-height: 1.7; }
</style>
