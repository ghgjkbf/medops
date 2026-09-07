<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiGet } from '../api/client'

interface ServerStatus { name: string; state: string; url?: string }

const servers = ref<ServerStatus[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const health = await apiGet<{ mcp_servers: ServerStatus[] }>('/health')
    servers.value = health.mcp_servers
  } finally {
    loading.value = false
  }
}

const stateTag = (state: string) =>
  state === 'connected' ? 'success' : state === 'unavailable' ? 'danger' : 'info'

onMounted(load)
</script>

<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <div class="head">
        <span>MCP 服务注册表</span>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
    </template>

    <el-table :data="servers" size="small">
      <el-table-column prop="name" label="服务名" width="180" />
      <el-table-column prop="url" label="端点" show-overflow-tooltip />
      <el-table-column prop="state" label="健康度" width="130">
        <template #default="{ row }">
          <el-tag :type="stateTag(row.state)" size="small">{{ row.state }}</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <el-alert
      class="mt16" type="info" :closable="false"
      title="每台模拟设备 = 独立 MCP Server（streamable-http）。注册表持久化于 mcp_server 表，健康度由连接状态实时反映。"
    />
  </el-card>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; }
.mt16 { margin-top: 16px; }
</style>
