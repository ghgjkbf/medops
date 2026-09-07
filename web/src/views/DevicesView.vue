<script setup lang="ts">
import { onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { apiGet } from '../api/client'

interface Device {
  device_id: string; device_type: string; model: string; department: string; status: string
}
interface Metric { metric_name: string; value: number; ts: string }

const rows = ref<Device[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const search = ref('')
const statusFilter = ref('')
const detail = ref<Device | null>(null)
const detailVisible = ref(false)
const metricChart = ref<HTMLElement | null>(null)
const metricName = ref('tube_temp')
let chart: echarts.ECharts | null = null

const statusType: Record<string, string> = {
  online: 'success', offline: 'info', error: 'danger',
}

async function load() {
  const body = await apiGet<{ total: number; items: Device[] }>('/devices', {
    page: page.value, page_size: pageSize.value, status: statusFilter.value || undefined,
  })
  rows.value = body.items
  total.value = body.total
}

function openDetail(row: Device) {
  detail.value = row
  detailVisible.value = true
  drawMetrics(row.device_id)
}

async function drawMetrics(deviceId: string) {
  const body = await apiGet<{ items: Metric[] }>('/metrics', {
    device_id: deviceId, metric_name: metricName.value || undefined, page_size: 100,
  })
  chart = chart || (metricChart.value ? echarts.init(metricChart.value) : null)
  if (!chart) return
  const pts = body.items.slice().reverse()
  chart.setOption({
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: pts.map(p => new Date(p.ts).toLocaleTimeString()) },
    yAxis: { type: 'value' },
    series: [{ type: 'line', smooth: true, data: pts.map(p => p.value), areaStyle: {} }],
  })
}

async function refreshMetrics() {
  if (detail.value) drawMetrics(detail.value.device_id)
}

onMounted(load)
</script>

<template>
  <div>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="search" placeholder="搜索设备 ID" clearable style="width: 220px" @input="load" />
        <el-select v-model="statusFilter" placeholder="状态" clearable style="width: 130px" @change="load">
          <el-option label="在线" value="online" />
          <el-option label="离线" value="offline" />
          <el-option label="异常" value="error" />
        </el-select>
      </div>

      <el-table :data="rows" size="small" @row-click="openDetail" class="mt12" style="cursor: pointer">
        <el-table-column prop="device_id" label="设备 ID" width="150" />
        <el-table-column prop="device_type" label="类型" width="110" />
        <el-table-column prop="model" label="型号" width="140" />
        <el-table-column prop="department" label="科室" width="140" />
        <el-table-column prop="status" label="状态">
          <template #default="{ row }">
            <el-tag :type="statusType[row.status] || 'info'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="mt12" layout="total, prev, pager, next" :total="total" :page-size="pageSize"
        v-model:current-page="page" @current-change="load"
      />
    </el-card>

    <el-drawer v-model="detailVisible" :title="detail?.device_id" size="45%">
      <template v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="类型">{{ detail.device_type }}</el-descriptions-item>
          <el-descriptions-item label="型号">{{ detail.model }}</el-descriptions-item>
          <el-descriptions-item label="科室">{{ detail.department }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType[detail.status] || 'info'" size="small">{{ detail.status }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <div class="mt16">
          <el-select v-model="metricName" size="small" style="width: 200px" @change="refreshMetrics">
            <el-option label="管球温度 tube_temp" value="tube_temp" />
            <el-option label="探测器温度 detector_temp" value="detector_temp" />
            <el-option label="氧浓度 o2" value="o2" />
          </el-select>
        </div>
        <div ref="metricChart" style="height: 260px; margin-top: 8px" />
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; }
.mt12 { margin-top: 12px; }
.mt16 { margin-top: 16px; }
</style>
