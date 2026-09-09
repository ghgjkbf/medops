<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiDelete, apiGet, apiPost } from '../api/client'

interface Alert {
  id: number; device_id: string; level: string; kind: string
  message: string; attribution: string | null; work_order_id: number | null; created_at: string
  meta?: { remediation?: Remedy[] }
}
interface Remedy {
  kind: string; rule?: string; need_consent?: boolean; executed?: boolean
  verified?: boolean; consent?: string; plan?: Record<string, unknown>; action?: Record<string, unknown>
  message?: string
}

const rows = ref<Alert[]>([])
const total = ref(0)
const page = ref(1)
const levelFilter = ref('')
const loading = ref(false)
const dialog = ref(false)
const current = ref<Alert | null>(null)
const remedies = ref<Remedy[]>([])
const confirming = ref(false)

function remedyKind(kind?: string) {
  return kind === 'platform_sw' ? '平台软件' : kind === 'device_sw' ? '设备软件' : kind === 'hardware' ? '硬件' : (kind ?? '—')
}
function remedyState(r?: Remedy) {
  if (!r) return ''
  if (r.consent === 'deny') return '已拒绝'
  if (r.executed && r.verified) return '已修复'
  if (r.executed && !r.verified) return '修复失败'
  if (r.need_consent) return '待确认'
  if (r.kind === 'hardware') return '方案'
  return '—'
}

async function show(row: Alert) {
  current.value = row
  const body = await apiGet<Remedy[]>(`/alerts/${row.id}/remediation`)
  remedies.value = body
  dialog.value = true
}

async function agree(r: Remedy) {
  if (!current.value) return
  confirming.value = true
  try {
    const body = await apiPost<Remedy>(`/alerts/${current.value.id}/remediation/agree`, {
      approve: true, rule: r.rule ?? null,
    })
    ElMessage.success(body.verified ? '处置完成并验证通过' : body.executed ? '处置已执行' : '已提交确认')
    dialog.value = false
    load()
  } finally {
    confirming.value = false
  }
}

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
      <el-table-column label="处置" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.meta?.remediation?.length"
                  :type="remedyState(row.meta.remediation[row.meta.remediation.length - 1]) === '已修复' ? 'success'
                        : remedyState(row.meta.remediation[row.meta.remediation.length - 1]) === '待确认' ? 'warning'
                        : remedyState(row.meta.remediation[row.meta.remediation.length - 1]) === '修复失败' ? 'danger'
                        : 'info'"
                  size="small" effect="plain">
            {{ remedyKind(row.meta.remediation[row.meta.remediation.length - 1]?.kind) }} · {{ remedyState(row.meta.remediation[row.meta.remediation.length - 1]) }}
          </el-tag>
          <el-button v-if="row.meta?.remediation?.length" size="small" type="primary" link @click="show(row)">详情</el-button>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-tag v-if="row.work_order_id" type="success" size="small">工单 #{{ row.work_order_id }}</el-tag>
          <el-button v-else size="small" type="primary" link @click="toWorkOrder(row)">转工单</el-button>
          <el-button size="small" type="danger" link @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" :title="`告警 #${current?.id} 处置建议`" width="560px">
      <el-empty v-if="!remedies.length" description="暂无处置建议" />
      <div v-for="(r, i) in remedies" :key="i" class="remedy-item">
        <div class="remedy-head">
          <el-tag size="small" effect="plain">{{ remedyKind(r.kind) }}</el-tag>
          <el-tag :type="remedyState(r) === '待确认' ? 'warning' : remedyState(r) === '已修复' ? 'success' : 'info'"
                  size="small">{{ remedyState(r) }}</el-tag>
          <span v-if="r.rule" class="rule">{{ r.rule }}</span>
        </div>
        <div v-if="r.plan" class="remedy-body">
          <p><b>原因：</b>{{ r.plan.cause }}</p>
          <ol>
            <li v-for="(s, si) in (r.plan.steps as string[])" :key="si">{{ s }}</li>
          </ol>
          <p><b>风险：</b>{{ r.plan.risk }} · <b>验证：</b>{{ r.plan.verification }}</p>
        </div>
        <div v-else-if="r.action" class="remedy-body">
          <p><b>处置动作：</b>{{ (r.action.params as Record<string, string>)?.name || r.action.task || r.action.op }}</p>
        </div>
        <div v-else class="remedy-body">{{ r.message }}</div>
        <el-button v-if="r.need_consent" type="primary" size="small" class="mt8"
                   :loading="confirming" @click="agree(r)">同意执行修复</el-button>
      </div>
    </el-dialog>

    <el-pagination
      class="mt12" layout="total, prev, pager, next" :total="total" :page-size="15"
      v-model:current-page="page" @current-change="load"
    />
  </el-card>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; }
.mt12 { margin-top: 12px; }
.mt8 { margin-top: 8px; }
.remedy-item { border-top: 1px solid var(--el-border-color-lighter); padding: 10px 0; }
.remedy-item:first-child { border-top: none; }
.remedy-head { display: flex; gap: 8px; align-items: center; }
.rule { color: var(--el-text-color-secondary); font-size: 12px; }
.remedy-body { margin-top: 6px; color: var(--el-text-color-primary); font-size: 13px; }
</style>
