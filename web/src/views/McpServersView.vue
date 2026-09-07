<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiDelete, apiGet, apiPost } from '../api/client'

interface ServerStatus {
  name: string; url: string; state: string
  tools: string[]; last_error: string | null
}

const servers = ref<ServerStatus[]>([])
const loading = ref(false)
const form = ref({ name: '', url: '' })
const submitting = ref(false)

async function load() {
  loading.value = true
  try {
    const body = await apiGet<{ items: ServerStatus[] }>('/mcp-servers')
    servers.value = body.items
  } finally {
    loading.value = false
  }
}

// registry states: healthy | unavailable | ... (NOT 'connected')
const stateTag = (state: string) =>
  state === 'healthy' ? 'success' : state === 'unavailable' ? 'danger' : 'info'

async function register() {
  if (!form.value.name || !form.value.url) {
    ElMessage.warning('请填写服务名与端点 URL')
    return
  }
  submitting.value = true
  try {
    const data = await apiPost<ServerStatus>('/mcp-servers', { ...form.value })
    if (data.state === 'healthy') {
      ElMessage.success(`已注册 ${data.name}，连接成功（${data.tools.length} 个工具）`)
    } else {
      ElMessage.warning(`已注册 ${data.name}，当前不可达（已保存，可稍后重试）`)
    }
    form.value = { name: '', url: '' }
    load()
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    ElMessage.error(detail || '注册失败')
  } finally {
    submitting.value = false
  }
}

async function remove(row: ServerStatus) {
  try {
    await ElMessageBox.confirm(
      `移除 MCP 服务「${row.name}」（${row.url}）？巡检与问答将不再使用该服务。`,
      '移除服务', { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
    )
  } catch { return }
  await apiDelete(`/mcp-servers/${row.name}`)
  ElMessage.success(`已移除 ${row.name}`)
  load()
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>快速配置</span>
        </div>
      </template>
      <div class="reg-form">
        <el-input v-model="form.name" placeholder="服务名，如 ct" style="width: 180px" />
        <el-input
          v-model="form.url" placeholder="端点 URL，如 http://127.0.0.1:8801/mcp"
          style="width: 340px" @keyup.enter="register"
        />
        <el-button type="primary" :loading="submitting" @click="register">注册</el-button>
      </div>
      <p class="hint">同名服务重复注册即更新端点；服务器暂不可达也允许注册（保存后显示 unavailable）。</p>
    </el-card>

    <el-card shadow="never" class="mt12">
      <template #header>
        <div class="head">
          <span>MCP 服务注册表（{{ servers.length }}）</span>
          <el-button size="small" @click="load">刷新</el-button>
        </div>
      </template>

      <el-table :data="servers" size="small">
        <el-table-column prop="name" label="服务名" width="160" />
        <el-table-column prop="url" label="端点" show-overflow-tooltip />
        <el-table-column label="健康度" width="120">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.last_error" :content="row.last_error || ''" placement="top">
              <el-tag :type="stateTag(row.state)" size="small">{{ row.state }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="工具" min-width="220">
          <template #default="{ row }">
            <el-tag v-for="t in row.tools.slice(0, 4)" :key="t" size="small" effect="plain" class="tool-tag">{{ t }}</el-tag>
            <span v-if="row.tools.length > 4" class="more">+{{ row.tools.length - 4 }}</span>
            <span v-if="!row.tools.length" class="more">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button size="small" type="danger" link @click="remove(row)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-alert
        class="mt16" type="info" :closable="false"
        title="每台模拟设备 = 独立 MCP Server（streamable-http）。注册表持久化于 mcp_server 表并即时生效，健康度由连接状态实时反映。"
      />
    </el-card>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; }
.mt12 { margin-top: 12px; }
.mt16 { margin-top: 16px; }
.reg-form { display: flex; gap: 10px; align-items: center; }
.hint { color: #909399; font-size: 12px; margin: 8px 0 0; }
.tool-tag { margin-right: 4px; }
.more { color: #909399; font-size: 12px; }
</style>
