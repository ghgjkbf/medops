<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiDelete, apiGet, apiPost } from '../api/client'

interface Alert {
  id: number; device_id: string; level: string; kind: string
  message: string; attribution: string | null; work_order_id: number | null; created_at: string
}

const rows = ref<Alert[]>([])
const total = ref(0)
const page = ref(1)
const levelFilter = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const body = await apiGet<{ total: number; items: Alert[] }>('/alerts', {
      page: page.value, page_size: 15, level: levelFilter.value || undefined,
    })
    rows.value = body.items
    total.value = body.total
  } finally {
    loading.value = false
  }
}

async function toWorkOrder(row: Alert) {
  await apiPost('/work-orders', {
    device_id: row.device_id,
    title: `Alert #${row.id}: ${row.message.slice(0, 80)}`,
    description: row.attribution ?? '',
  })
  ElMessage.success('已创建工单')
  load()
}

async function remove(row: Alert) {
  try {
    await ElMessageBox.confirm(
      `删除告警 #${row.id}「${row.message}」？`, '删除告警',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  await apiDelete(`/alerts/${row.id}`)
  ElMessage.success(`告警 #${row.id} 已删除`)
  load()
}

async function clearAll() {
  try {
    await ElMessageBox.confirm(
      '清空全部告警（含已解决与未解决）？此操作不可恢复。', '清空告警',
      { type: 'error', confirmButtonText: '清空', cancelButtonText: '取消' },
    )
  } catch { return }
  const { deleted } = await apiDelete<{ deleted: number }>('/alerts')
  ElMessage.success(`已清理 ${deleted} 条告警`)
  load()
}

onMounted(load)
</script>

<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-select v-model="levelFilter" placeholder="级别" clearable style="width: 140px" @change="load">
        <el-option label="critical" value="critical" />
        <el-option label="warning" value="warning" />
        <el-option label="info" value="info" />
      </el-select>
      <el-button type="danger" plain @click="clearAll">清空告警</el-button>
    </div>

    <el-table :data="rows" size="small" v-loading="loading" class="mt12">
      <el-table-column prop="created_at" label="时间" width="175">
        <template #default="{ row }">{{ new Date(row.created_at).toLocaleString() }}</template>
      </el-table-column>
      <el-table-column prop="device_id" label="设备" width="120" />
      <el-table-column prop="level" label="级别" width="95">
        <template #default="{ row }">
          <el-tag :type="row.level === 'critical' ? 'danger' : row.level === 'warning' ? 'warning' : 'info'" size="small">
            {{ row.level }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="kind" label="类别" width="140">
        <template #default="{ row }">
          <el-tag v-if="row.kind === 'maintenance_due'" type="warning" size="small" effect="plain">维保到期</el-tag>
          <el-tag v-else size="small" effect="plain">故障</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="message" label="内容" show-overflow-tooltip />
      <el-table-column prop="attribution" label="归因" show-overflow-tooltip />
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-tag v-if="row.work_order_id" type="success" size="small">工单 #{{ row.work_order_id }}</el-tag>
          <el-button v-else size="small" type="primary" link @click="toWorkOrder(row)">转工单</el-button>
          <el-button size="small" type="danger" link @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      class="mt12" layout="total, prev, pager, next" :total="total" :page-size="15"
      v-model:current-page="page" @current-change="load"
    />
  </el-card>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; }
.mt12 { margin-top: 12px; }
</style>
