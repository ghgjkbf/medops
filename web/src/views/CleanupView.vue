<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiDelete, apiGet } from '../api/client'

interface Count { total: number }

const counts = ref({ alerts: 0, logs: 0, metrics: 0 })
const days = ref(30)
const loading = ref(false)

function beforeIso(daysAgo: number): string {
  return new Date(Date.now() - daysAgo * 86400_000).toISOString()
}

async function loadCounts() {
  const [a, l, m] = await Promise.all([
    apiGet<Count>('/alerts', { page_size: 1 }),
    apiGet<Count>('/logs', { page_size: 1 }),
    apiGet<Count>('/metrics', { page_size: 1 }),
  ])
  counts.value = { alerts: a.total, logs: l.total, metrics: m.total }
}

async function cleanup(resource: 'alerts' | 'logs' | 'metrics', label: string) {
  try {
    await ElMessageBox.confirm(
      `清理 ${days.value} 天前的${label}？此操作不可恢复。`, `清理${label}`,
      { type: 'warning', confirmButtonText: '清理', cancelButtonText: '取消' },
    )
  } catch { return }
  loading.value = true
  try {
    const { deleted } = await apiDelete<{ deleted: number }>(`/${resource}`, {
      before: beforeIso(days.value),
    })
    ElMessage.success(`已清理 ${deleted} 条${label}`)
    loadCounts()
  } finally {
    loading.value = false
  }
}

async function clearChat() {
  try {
    await ElMessageBox.confirm(
      '清空全部会话历史（含工具调用轨迹）？此操作不可恢复。', '清空会话',
      { type: 'error', confirmButtonText: '清空', cancelButtonText: '取消' },
    )
  } catch { return }
  const { deleted } = await apiDelete<{ deleted: number }>('/chat-sessions')
  ElMessage.success(`已清空 ${deleted} 个会话`)
}

onMounted(loadCounts)
</script>

<template>
  <div v-loading="loading">
    <el-card shadow="never">
      <template #header>冗余数据清理</template>
      <p class="hint">
        告警、日志、指标随时间累积会占用存储；选择时间范围批量清理，或清空会话历史。删除不可恢复。
      </p>

      <div class="controls">
        <span class="label">清理范围</span>
        <el-radio-group v-model="days">
          <el-radio-button :value="7">7 天前</el-radio-button>
          <el-radio-button :value="30">30 天前</el-radio-button>
          <el-radio-button :value="90">90 天前</el-radio-button>
        </el-radio-group>
      </div>

      <el-row :gutter="12" class="mt12">
        <el-col :span="8">
          <el-statistic title="告警总数" :value="counts.alerts" />
          <el-button class="mt8" type="warning" @click="cleanup('alerts', '告警')">清理告警</el-button>
        </el-col>
        <el-col :span="8">
          <el-statistic title="日志总数" :value="counts.logs" />
          <el-button class="mt8" type="warning" @click="cleanup('logs', '日志')">清理日志</el-button>
        </el-col>
        <el-col :span="8">
          <el-statistic title="指标总数" :value="counts.metrics" />
          <el-button class="mt8" type="warning" @click="cleanup('metrics', '指标')">清理指标</el-button>
        </el-col>
      </el-row>

      <el-divider />

      <div class="controls">
        <el-button type="danger" plain @click="clearChat">清空会话历史</el-button>
        <span class="hint-inline">会话消息包含问答与工具调用轨迹，清空后无法追溯。</span>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.hint { color: #909399; font-size: 13px; margin: 0 0 12px; }
.hint-inline { color: #909399; font-size: 12px; margin-left: 8px; }
.controls { display: flex; align-items: center; gap: 12px; }
.label { font-size: 13px; color: #606266; }
.mt12 { margin-top: 12px; }
.mt8 { margin-top: 8px; width: 100%; }
</style>
