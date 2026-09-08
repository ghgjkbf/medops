<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadUserFile } from 'element-plus'
import { apiDelete, apiGet, apiPost, apiUpload } from '../api/client'

interface Doc {
  id: number; title: string; content: string
  meta: Record<string, string>; score?: number
}

interface KSource {
  id: number; name: string; type: string; url: string
  schedule_minutes: number | null; last_synced_at: string | null
  status: string; meta: Record<string, unknown>
}

const docs = ref<Doc[]>([])
const query = ref('')
const fileList = ref<UploadUserFile[]>([])
const loading = ref(false)
const backend = ref('keyword')
const sources = ref<KSource[]>([])
const srcForm = ref({ name: '', type: 'web', url: '', schedule_minutes: undefined as number | undefined })

async function loadSources() {
  const body = await apiGet<{ items: KSource[] }>('/knowledge-sources')
  sources.value = body.items
}

async function load(q?: string) {
  loading.value = true
  try {
    const body = await apiGet<{ items: Doc[]; backend: string }>('/knowledge', q ? { q } : {})
    docs.value = body.items
    backend.value = body.backend || 'keyword'
  } finally {
    loading.value = false
  }
}

async function addSource() {
  if (!srcForm.value.name || !srcForm.value.url) {
    ElMessage.warning('名称与 URL 必填')
    return
  }
  try {
    await apiPost('/knowledge-sources', { ...srcForm.value })
    ElMessage.success('知识源已绑定')
    srcForm.value = { name: '', type: 'web', url: '', schedule_minutes: undefined }
    loadSources()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '绑定失败')
  }
}

async function syncSource(row: KSource) {
  const data = await apiPost<{ ok: boolean; added: number; error?: string }>(
    `/knowledge-sources/${row.id}/sync`)
  if (data.ok) ElMessage.success(`已同步，新增 ${data.added} 条`)
  else ElMessage.error(data.error || '同步失败')
  loadSources()
}

async function removeSource(row: KSource) {
  try {
    await ElMessageBox.confirm(`解绑知识源「${row.name}」？已导入的文档会保留。`, '解绑知识源',
      { type: 'warning', confirmButtonText: '解绑', cancelButtonText: '取消' })
  } catch { return }
  await apiDelete(`/knowledge-sources/${row.id}`)
  ElMessage.success('已解绑')
  loadSources()
}

async function search() {
  await load(query.value || undefined)
}

async function remove(doc: Doc) {
  try {
    await ElMessageBox.confirm(
      `删除知识文档「${doc.title}」？`, '删除文档',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  await apiDelete(`/knowledge/${doc.id}`)
  ElMessage.success('文档已删除')
  load(query.value || undefined)
}

async function onChange(_file: unknown, files: UploadUserFile[]) {
  if (!files.length) return
  const f = files[files.length - 1].raw
  fileList.value = [] // allow re-uploading the same file
  if (!f) return
  const form = new FormData()
  form.append('file', f)
  try {
    const data = await apiUpload<{ title: string; chars: number }>('/knowledge/import', form)
    ElMessage.success(`已导入「${data.title}」（${data.chars} 字符）`)
    load(query.value || undefined)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '导入失败（限 512KB 文本文件）')
  }
}

onMounted(() => { load(); loadSources() })
</script>

<template>
  <div v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>
            知识库（故障原因与处理方法）
            <el-tag size="small" effect="plain" class="src">检索后端：{{ backend }}</el-tag>
          </span>
          <el-upload
            :file-list="fileList" :auto-upload="false" :show-file-list="false"
            :on-change="onChange" accept=".txt,.md,.csv,.json"
          >
            <el-button size="small" type="primary">导入文件</el-button>
          </el-upload>
        </div>
      </template>

      <div class="toolbar">
        <el-input
          v-model="query" placeholder="搜索故障原因 / 处理方法，如：球管过热"
          clearable style="width: 340px" @keyup.enter="search"
        />
        <el-button type="primary" plain @click="search">搜索</el-button>
        <el-button @click="query = ''; load()">全部</el-button>
      </div>

      <el-table :data="docs" size="small" class="mt12">
        <el-table-column label="标题" min-width="240">
          <template #default="{ row }">
            {{ row.title }}
            <el-tag v-if="row.meta?.source === 'builtin'" size="small" effect="plain" class="src">内置</el-tag>
            <el-tag v-else-if="row.meta?.source === 'upload'" size="small" type="success" effect="plain" class="src">导入</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="内容摘要" show-overflow-tooltip>
          <template #default="{ row }">{{ row.content.slice(0, 80) }}</template>
        </el-table-column>
        <el-table-column label="相关度" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.score != null" size="small" type="warning">{{ row.score }}</el-tag>
            <span v-else>—</span>
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
        title="内置文档覆盖 9 类故障剧本的原因与处理方法，平台启动时自动加载。智能问答与巡检归因会自动检索知识库。"
      />
    </el-card>

    <el-card shadow="never" class="mt12">
      <template #header>
        <div class="head">
          <span>外部知识源绑定（{{ sources.length }}）</span>
        </div>
      </template>

      <div class="reg-form">
        <el-input v-model="srcForm.name" placeholder="名称" style="width: 140px" />
        <el-select v-model="srcForm.type" style="width: 130px">
          <el-option label="网页 web" value="web" />
          <el-option label="订阅 RSS" value="rss" />
          <el-option label="向量库文件" value="vector_store" />
        </el-select>
        <el-input
          v-model="srcForm.url" placeholder="URL / 向量库文件路径"
          style="width: 300px"
        />
        <el-button type="primary" @click="addSource">绑定</el-button>
      </div>
      <p class="hint">
        支持网页/RSS 抓取（自动去正文标签、分块、按内容哈希去重）与外部向量库文件导入
        （sqlite 表 docs(id,title,content)）。禁止内网地址（SSRF 防护）。
      </p>

      <el-table :data="sources" size="small" class="mt12">
        <el-table-column prop="name" label="名称" width="140" />
        <el-table-column prop="type" label="类型" width="110" />
        <el-table-column prop="url" label="地址" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ok' ? 'success' : row.status === 'error' ? 'danger' : 'info'" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="上次同步" width="160">
          <template #default="{ row }">
            {{ row.last_synced_at ? new Date(row.last_synced_at).toLocaleString() : '—' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="syncSource(row)">立即同步</el-button>
            <el-button size="small" type="danger" link @click="removeSource(row)">解绑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; }
.toolbar { display: flex; gap: 10px; }
.mt12 { margin-top: 12px; }
.mt16 { margin-top: 16px; }
.src { margin-left: 6px; }
</style>
