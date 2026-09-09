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
onMounted(load)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="head">
        <span>Agent 插件（内置技能）</span>
        <el-tag size="small" type="info" effect="plain">5 项内置 · 全部可离线运行</el-tag>
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
  </el-card>
</template>

<style scoped>
.head { display: flex; gap: 10px; align-items: center; }
.mt12 { margin-top: 12px; }
</style>
