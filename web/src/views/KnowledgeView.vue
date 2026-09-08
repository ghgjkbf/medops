<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadUserFile } from 'element-plus'
import { apiDelete, apiGet, apiUpload } from '../api/client'

interface Doc {
  id: number; title: string; content: string
  meta: Record<string, string>; score?: number
}

const docs = ref<Doc[]>([])
const query = ref('')
const fileList = ref<UploadUserFile[]>([])
const loading = ref(false)
const backend = ref('keyword')

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

onMounted(() => load())
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
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; }
.toolbar { display: flex; gap: 10px; }
.mt12 { margin-top: 12px; }
.mt16 { margin-top: 16px; }
.src { margin-left: 6px; }
</style>
