<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiDelete, apiGetRaw, apiPatch, apiPut, getApiKey, setApiKey } from '../api/client'

interface ExternalEndpoint {
  name: string; base_url: string; api_key: string
  auth_type: string; api_header: string | null
  model: string | null; kind: string; enabled: boolean
}

const endpoints = ref<ExternalEndpoint[]>([])
const loading = ref(false)
const dialogVisible = ref(false)

const emptyForm = {
  name: '', base_url: '', api_key: '', auth_type: 'bearer',
  api_header: '', kind: 'generic', model: '', enabled: true,
}
const form = reactive({ ...emptyForm })

const localKey = ref(getApiKey())

const authKeys = computed(() => endpoints.value.some((e) => e.api_key && e.enabled))

async function load() {
  loading.value = true
  try {
    // /endpoints returns {count, endpoints} — not the {ok,data} envelope
    const body = await apiGetRaw<{ count: number; endpoints: ExternalEndpoint[] }>('/endpoints')
    endpoints.value = body.endpoints
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, emptyForm)
  dialogVisible.value = true
}

async function submit() {
  if (!form.name || !form.base_url) {
    ElMessage.warning('名称与 base_url 必填')
    return
  }
  const payload: Record<string, unknown> = {
    name: form.name, base_url: form.base_url, api_key: form.api_key,
    auth_type: form.auth_type, kind: form.kind, enabled: form.enabled,
  }
  if (form.auth_type === 'header') payload.api_header = form.api_header
  if (form.kind === 'llm') payload.model = form.model
  await apiPut('/endpoints', payload)
  ElMessage.success(`端点 ${form.name} 已保存`)
  dialogVisible.value = false
  // armed inbound auth would lock this UI out — offer to store the key locally
  if (form.api_key) {
    try {
      await ElMessageBox.confirm(
        '该端点带 Key，入站鉴权已开启（全部 /api/v1 接口将要求 X-API-Key）。把此 Key 存入本前端以继续访问？',
        '保存本地 Key', { type: 'warning', confirmButtonText: '存入本前端', cancelButtonText: '稍后手动填' },
      )
      setApiKey(form.api_key)
      localKey.value = form.api_key
      ElMessage.success('本地 Key 已保存')
    } catch { /* user chose manual */ }
  }
  load()
}

async function toggle(row: ExternalEndpoint, enabled: boolean) {
  try {
    await apiPatch(`/endpoints/${row.name}`, { enabled })
    ElMessage.success(`${row.name} 已${enabled ? '启用' : '停用'}`)
  } catch {
    row.enabled = !enabled // revert switch on failure
  }
  load()
}

async function remove(row: ExternalEndpoint) {
  try {
    await ElMessageBox.confirm(
      `删除端点「${row.name}」？Agent 将无法再调用该外部 API；若其 Key 是最后一个入站凭据，鉴权将回到开放模式。`,
      '删除端点', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  await apiDelete(`/endpoints/${row.name}`)
  ElMessage.success(`端点 ${row.name} 已删除`)
  load()
}

function saveLocalKey() {
  setApiKey(localKey.value)
  ElMessage.success(localKey.value ? '本地 Key 已保存' : '本地 Key 已清空（回到开放模式）')
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>外部 API 接入（{{ endpoints.length }}）</span>
          <div class="head-actions">
            <el-input
              v-model="localKey" size="small" :placeholder="authKeys ? '本前端 X-API-Key' : '开放模式，无需 Key'"
              style="width: 220px" show-password clearable
            />
            <el-button size="small" @click="saveLocalKey">保存 Key</el-button>
            <el-button size="small" @click="load">刷新</el-button>
            <el-button size="small" type="primary" @click="openCreate">接入端点</el-button>
          </div>
        </div>
      </template>

      <el-alert
        v-if="authKeys && !localKey" class="mb12" type="warning" :closable="false"
        title="存在已启用的带 Key 端点：入站鉴权已开启，请把任一端点的 Key 填入上方「X-API-Key」并保存，否则本前端请求会被 401 拒绝。"
      />

      <el-table :data="endpoints" size="small">
        <el-table-column prop="name" label="名称" width="140" />
        <el-table-column prop="base_url" label="Base URL" show-overflow-tooltip />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag :type="row.kind === 'llm' ? 'warning' : 'info'" size="small" effect="plain">
              {{ row.kind === 'llm' ? 'LLM' : '通用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="auth_type" label="鉴权" width="90" />
        <el-table-column label="Key" width="80">
          <template #default="{ row }">
            <span v-if="row.api_key" class="masked">{{ row.api_key }}</span>
            <span v-else class="none">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="endpoints.some((e) => e.kind === 'llm')" prop="model" label="模型" width="120">
          <template #default="{ row }">{{ row.model || '—' }}</template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v: any) => toggle(row, !!v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" type="danger" link @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-alert
        class="mt16" type="info" :closable="false"
        title="kind=llm 的端点自动并入 LLM 降级链；任意端点可被 Agent 通过 call_external_api 工具调用。Key 永不回显（***）。"
      />
    </el-card>

    <el-dialog v-model="dialogVisible" title="接入外部 API 端点" width="500px">
      <el-form label-width="100px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="如 his / pacs / qwen" /></el-form-item>
        <el-form-item label="Base URL"><el-input v-model="form.base_url" placeholder="https://host/api" /></el-form-item>
        <el-form-item label="API Key"><el-input v-model="form.api_key" type="password" show-password placeholder="留空 = 无鉴权" /></el-form-item>
        <el-form-item label="鉴权方式">
          <el-radio-group v-model="form.auth_type">
            <el-radio value="bearer">Bearer</el-radio>
            <el-radio value="header">自定义头</el-radio>
            <el-radio value="none">无</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.auth_type === 'header'" label="头名称">
          <el-input v-model="form.api_header" placeholder="如 X-HIS-Key" />
        </el-form-item>
        <el-form-item label="端点类型">
          <el-radio-group v-model="form.kind">
            <el-radio value="generic">通用 API</el-radio>
            <el-radio value="llm">LLM（并入降级链）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.kind === 'llm'" label="模型名">
          <el-input v-model="form.model" placeholder="如 deepseek-chat" />
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; }
.head-actions { display: flex; gap: 8px; align-items: center; }
.mb12 { margin-bottom: 12px; }
.mt16 { margin-top: 16px; }
.masked { color: #909399; letter-spacing: 2px; }
.none { color: #c0c4cc; }
</style>
