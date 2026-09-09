<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiGet, apiPost } from '../api/client'

interface Plugin {
  name: string; description: string; risk: string
  needs_gate: string; enabled: boolean; gate_ok: boolean
}

const items = ref<Plugin[]>([])
const loading = ref(false)
const dialog = ref(false)
const importing = ref(false)
const form = ref({ name: '', description: '', risk: 'safe', kind: 'prompt_template', config: '{\n  "template": "巡检 {device} 重点看 {focus}"\n}' })

async function load() {
  loading.value = true
  try {
    const body = await apiGet<{ items: Plugin[] }>('/plugins')
    items.value = body.items
  } finally { loading.value = false }
}

async function toggle(p: Plugin) {
  await apiPost(`/plugins/${p.name}`, { enabled: !p.enabled })
  ElMessage.success(`插件 ${p.name} 已${p.enabled ? '停用' : '启用'}`)
  load()
}

async function doImport() {
  importing.value = true
  try {
    let config: Record<string, unknown> = {}
    try { config = JSON.parse(form.value.config) } catch { /* form shows error below */ }
    await apiPost('/plugins/import', { ...form.value, config })
    ElMessage.success(`插件 ${form.value.name} 已导入`)
    dialog.value = false
    load()
  } finally {
    importing.value = false
  }
}
onMounted(load)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="head">
        <span>Agent 插件（内置技能）</span>
        <el-button size="small" type="primary" plain @click="dialog = true">导入插件</el-button>
      </div>
    </template>

    <el-alert class="mt12" type="info" :closable="false" title=""
              description="提示词生成 / 代码生成与检查 / 寻找工具为安全技能；电脑控制与电脑搜索需设置对应环境变量（MEDOPS_PLUGIN_CONSOLE / MEDOPS_PLUGIN_SEARCH）才会执行。" />

    <el-table :data="items" size="small" v-loading="loading" class="mt12">
      <el-table-column prop="name" label="技能" width="180" />
      <el-table-column prop="description" label="说明" show-overflow-tooltip />
      <el-table-column prop="risk" label="风险" width="110">
        <template #default="{ row }">
          <el-tag :type="row.risk === 'system' ? 'danger' : row.risk === 'triggered' ? 'warning' : 'success'"
                  size="small" effect="plain">{{ row.risk }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="门控" width="150">
        <template #default="{ row }">
          <el-tag v-if="row.needs_gate" :type="row.gate_ok ? 'success' : 'warning'" size="small" effect="plain">
            {{ row.gate_ok ? '门控已开' : '默认关闭' }}
          </el-tag>
          <el-tag v-else size="small" effect="plain">无</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="100">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" :disabled="!row.gate_ok && !!row.needs_gate"
                     @change="toggle(row)" />
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" title="导入插件" width="520px">
      <el-form label-width="90px" size="small">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="demo_ext（字母/数字/_/-）" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" placeholder="插件用途说明" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.kind" style="width: 100%">
            <el-option label="prompt_template（提示词模板）" value="prompt_template" />
            <el-option label="code_template（修复脚本模板）" value="code_template" />
            <el-option label="tool_chain（设备动作链）" value="tool_chain" />
            <el-option label="kb_query（知识检索模板）" value="kb_query" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险">
          <el-select v-model="form.risk" style="width: 100%">
            <el-option label="safe" value="safe" />
            <el-option label="triggered" value="triggered" />
            <el-option label="system（需门控环境变量）" value="system" />
          </el-select>
        </el-form-item>
        <el-form-item label="配置 JSON">
          <el-input v-model="form.config" type="textarea" :rows="6" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="dialog = false">取消</el-button>
        <el-button size="small" type="primary" :loading="importing" @click="doImport">导入</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.head { display: flex; gap: 10px; align-items: center; justify-content: space-between; }
.mt12 { margin-top: 12px; }
</style>
