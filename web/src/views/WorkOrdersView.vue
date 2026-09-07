<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiDelete, apiGet, apiPatch } from '../api/client'

interface WorkOrder {
  id: number; device_id: string; title: string; description: string; status: string
}

// Same machine as backend (medops_common.constants.WORK_ORDER_TRANSITIONS).
const TRANSITIONS: Record<string, string[]> = {
  pending: ['in_progress'],
  in_progress: ['awaiting_verification'],
  awaiting_verification: ['closed'],
  closed: [],
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待接单', in_progress: '处理中',
  awaiting_verification: '待验证', closed: '已关闭',
}

const orders = ref<WorkOrder[]>([])
const loading = ref(false)
const statusFilter = ref('')

const columns = computed(() => {
  const byStatus: Record<string, WorkOrder[]> = {
    pending: [], in_progress: [], awaiting_verification: [], closed: [],
  }
  for (const o of orders.value) {
    if (!statusFilter.value || o.status === statusFilter.value) {
      byStatus[o.status]?.push(o)
    }
  }
  return byStatus
})

function legalNext(status: string): string[] {
  return TRANSITIONS[status] ?? []
}

async function load() {
  loading.value = true
  try {
    const body = await apiGet<{ items: WorkOrder[] }>('/work-orders', { page_size: 200 })
    orders.value = body.items
  } finally {
    loading.value = false
  }
}

async function transition(order: WorkOrder, next: string) {
  try {
    await apiPatch(`/work-orders/${order.id}`, { status: next })
    ElMessage.success(`# ${order.id} -> ${STATUS_LABEL[next]}`)
    load()
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 409) ElMessage.error('非法状态迁移')
    else if (status === 422) ElMessage.error('未知状态')
    else ElMessage.error('请求失败')
  }
}

async function remove(order: WorkOrder) {
  try {
    await ElMessageBox.confirm(
      `删除工单 #${order.id}「${order.title}」？关联的告警/维保记录将解除绑定（保留）。`,
      '删除工单', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  await apiDelete(`/work-orders/${order.id}`)
  ElMessage.success(`工单 #${order.id} 已删除`)
  load()
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-radio-group v-model="statusFilter" @change="load">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="pending">待接单</el-radio-button>
        <el-radio-button value="in_progress">处理中</el-radio-button>
        <el-radio-button value="awaiting_verification">待验证</el-radio-button>
        <el-radio-button value="closed">已关闭</el-radio-button>
      </el-radio-group>
    </div>

    <div class="board">
      <el-card v-for="(list, status) in columns" :key="status" shadow="never" class="col">
        <template #header>{{ STATUS_LABEL[status] }}（{{ list.length }}）</template>
        <el-empty v-if="!list.length" description=" " :image-size="40" />
        <el-card v-for="o in list" :key="o.id" shadow="hover" class="wo">
          <div class="wo-title">#{{ o.id }} · {{ o.device_id }}</div>
          <div class="wo-desc">{{ o.title }}</div>
          <div class="wo-actions">
            <el-button
              v-for="next in legalNext(o.status)" :key="next" size="small"
              :type="next === 'closed' ? 'success' : 'primary'" @click="transition(o, next)"
            >
              {{ STATUS_LABEL[next] }}
            </el-button>
            <el-tag v-if="!legalNext(o.status).length" type="info" size="small">终态</el-tag>
            <el-button size="small" type="danger" plain @click="remove(o)">删除</el-button>
          </div>
        </el-card>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.toolbar { margin-bottom: 12px; }
.board { display: flex; gap: 12px; align-items: flex-start; }
.col { flex: 1; min-height: 300px; }
.wo { margin-bottom: 10px; }
.wo-title { font-weight: 600; font-size: 12px; color: #909399; }
.wo-desc { margin: 6px 0; font-size: 13px; }
.wo-actions { display: flex; gap: 6px; flex-wrap: wrap; }
</style>
