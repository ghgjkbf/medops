<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { apiGet, connectDashboard } from '../api/client'

interface Device { device_id: string; device_type: string; status: string; department: string }
interface Alert { id: number; device_id: string; level: string; kind: string; message: string; created_at: string }
interface ServerStatus { name: string; state: string }

const devices = ref<Device[]>([])
const alerts = ref<Alert[]>([])
const servers = ref<ServerStatus[]>([])
const wsLive = ref(false)
let chart: echarts.ECharts | null = null
let closeWs: (() => void) | null = null

const statusType: Record<string, string> = {
  online: 'success', offline: 'info', error: 'danger', unknown: 'warning',
}

async function load() {
  devices.value = (await apiGet<{ items: Device[] }>('/devices', { page_size: 100 })).items
  alerts.value = (await apiGet<{ items: Alert[] }>('/alerts', { page_size: 8 })).items
  const health = await apiGet<{ mcp_servers: ServerStatus[] }>('/health')
  servers.value = health.mcp_servers
  renderChart()
}

function renderChart() {
  const el = document.getElementById('alert-pie')
  if (!el) return
  chart = chart || echarts.init(el)
  const counts: Record<string, number> = {}
  for (const a of alerts.value) counts[a.level] = (counts[a.level] || 0) + 1
  chart.setOption({
    tooltip: { trigger: 'item' },
    series: [{
      type: 'pie', radius: ['40%', '70%'], label: { show: false },
      data: Object.entries(counts).map(([name, value]) => ({ name, value })),
    }],
  })
}

onMounted(async () => {
  await load()
  closeWs = connectDashboard((msg) => {
    if (msg.type === 'alert') {
      alerts.value.unshift(msg)
      renderChart()
    } else if (msg.type === 'status') {
      servers.value = msg.registry ?? []
      wsLive.value = true
    }
  })
})

onUnmounted(() => {
  closeWs?.()
  chart?.dispose()
})
</script>

<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>设备总数</template>
          <el-statistic :value="devices.length" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>在线</template>
          <el-statistic :value="devices.filter(d => d.status === 'online').length"
                        :value-style="{ color: '#67c23a' }" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>异常</template>
          <el-statistic :value="devices.filter(d => d.status === 'error').length"
                        :value-style="{ color: '#f56c6c' }" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>MCP 服务</template>
          <el-statistic :value="servers.length" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="mt16">
      <el-col :span="10">
        <el-card shadow="hover">
          <template #header>最近告警级别分布</template>
          <div id="alert-pie" style="height: 240px" />
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card shadow="hover">
          <template #header>
            <div class="card-head">
              <span>实时告警流</span>
              <el-tag :type="wsLive ? 'success' : 'info'" size="small">
                {{ wsLive ? 'WS 已连接' : '轮询模式' }}
              </el-tag>
            </div>
          </template>
          <el-table :data="alerts" size="small" max-height="240">
            <el-table-column prop="created_at" label="时间" width="180">
              <template #default="{ row }">{{ new Date(row.created_at).toLocaleString() }}</template>
            </el-table-column>
            <el-table-column prop="device_id" label="设备" width="120" />
            <el-table-column prop="level" label="级别" width="90">
              <template #default="{ row }">
                <el-tag :type="row.level === 'critical' ? 'danger' : row.level === 'warning' ? 'warning' : 'info'" size="small">
                  {{ row.level }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="message" label="内容" show-overflow-tooltip />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="hover" class="mt16">
      <template #header>设备状态</template>
      <el-table :data="devices" size="small">
        <el-table-column prop="device_id" label="ID" width="140" />
        <el-table-column prop="device_type" label="类型" width="110" />
        <el-table-column prop="department" label="科室" width="130" />
        <el-table-column prop="status" label="状态">
          <template #default="{ row }">
            <el-tag :type="statusType[row.status] || 'info'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.mt16 { margin-top: 16px; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
</style>
