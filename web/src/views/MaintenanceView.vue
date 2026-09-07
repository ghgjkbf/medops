<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiGet, apiPost } from '../api/client'

interface Plan {
  id: number; device_id: string; name: string; interval_days: number
  last_done_at: string | null; active: boolean
}
interface Record_ {
  id: number; device_id: string; kind: string; content: string
  performed_by: string; performed_at: string
}

const plans = ref<Plan[]>([])
const records = ref<Record_[]>([])
const dialogVisible = ref(false)
const form = ref({ device_id: '', name: '', interval_days: 30 })
const recordForm = ref({ device_id: '', kind: 'pm', content: '', performed_by: '' })
const recordDialogVisible = ref(false)

const now = Date.now()
const dueDate = (p: Plan): number | null =>
  p.last_done_at ? new Date(p.last_done_at).getTime() + p.interval_days * 86400_000 : null
const isDue = (p: Plan): boolean => {
  const d = dueDate(p)
  return d !== null && d <= now
}

const duePlans = computed(() => plans.value.filter(isDue).length)

async function load() {
  plans.value = (await apiGet<{ items: Plan[] }>('/maintenance-plans', { page_size: 100 })).items
  records.value = (await apiGet<{ items: Record_[] }>('/maintenance-records', { page_size: 50 })).items
}

async function createPlan() {
  await apiPost('/maintenance-plans', form.value)
  ElMessage.success('计划已创建')
  dialogVisible.value = false
  load()
}

async function addRecord() {
  await apiPost('/maintenance-records', recordForm.value)
  ElMessage.success('维保记录已登记')
  recordDialogVisible.value = false
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="14">
        <el-card shadow="never">
          <template #header>
            <div class="head">
              <span>维保计划 <el-badge v-if="duePlans" :value="`${duePlans} 项到期`" type="warning" /></span>
              <el-button size="small" type="primary" @click="dialogVisible = true">新建计划</el-button>
            </div>
          </template>
          <el-table
            :data="plans" size="small"
            :row-class-name="(r: any) => (isDue(r.row) ? 'due-row' : '')"
          >
            <el-table-column prop="device_id" label="设备" width="120" />
            <el-table-column prop="name" label="计划" />
            <el-table-column prop="interval_days" label="周期(天)" width="90" />
            <el-table-column label="上次执行" width="165">
              <template #default="{ row }">
                {{ row.last_done_at ? new Date(row.last_done_at).toLocaleString() : '—' }}
              </template>
            </el-table-column>
            <el-table-column label="到期" width="80">
              <template #default="{ row }">
                <el-tag v-if="isDue(row)" type="warning" size="small">到期</el-tag>
                <el-tag v-else type="success" size="small">正常</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="10">
        <el-card shadow="never">
          <template #header>
            <div class="head">
              <span>维保记录</span>
              <el-button size="small" type="primary" plain @click="recordDialogVisible = true">登记记录</el-button>
            </div>
          </template>
          <el-table :data="records" size="small" max-height="420">
            <el-table-column prop="performed_at" label="时间" width="160">
              <template #default="{ row }">{{ new Date(row.performed_at).toLocaleString() }}</template>
            </el-table-column>
            <el-table-column prop="device_id" label="设备" width="110" />
            <el-table-column prop="kind" label="类别" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="row.kind === 'repair' ? 'danger' : 'primary'" effect="plain">
                  {{ row.kind === 'repair' ? '维修' : '保养' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="content" label="内容" show-overflow-tooltip />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="dialogVisible" title="新建维保计划" width="420px">
      <el-form label-width="90px">
        <el-form-item label="设备 ID"><el-input v-model="form.device_id" /></el-form-item>
        <el-form-item label="计划名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="周期(天)"><el-input-number v-model="form.interval_days" :min="1" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="createPlan">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="recordDialogVisible" title="登记维保记录" width="460px">
      <el-form label-width="90px">
        <el-form-item label="设备 ID"><el-input v-model="recordForm.device_id" /></el-form-item>
        <el-form-item label="类别">
          <el-radio-group v-model="recordForm.kind">
            <el-radio value="pm">保养</el-radio>
            <el-radio value="repair">维修</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="内容"><el-input v-model="recordForm.content" type="textarea" /></el-form-item>
        <el-form-item label="执行人"><el-input v-model="recordForm.performed_by" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="recordDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="addRecord">登记</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; }
:deep(.due-row) { background: #fdf6ec; }
</style>
