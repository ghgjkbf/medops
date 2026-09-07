<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { chatOnce } from '../api/client'

interface TraceStep { tool: string; args?: unknown; ok?: boolean }
interface Bubble {
  role: 'user' | 'assistant'
  text: string
  steps?: TraceStep[]
  provider?: string
}

const bubbles = ref<Bubble[]>([])
const input = ref('')
const busy = ref(false)
const listEl = ref<HTMLElement | null>(null)

const suggestions = ['3号CT状态怎么样', '最近有什么告警', 'CT 管球温度异常是什么原因', '哪些设备该保养了']

async function send(text?: string) {
  const message = (text ?? input.value).trim()
  if (!message || busy.value) return
  input.value = ''
  busy.value = true
  bubbles.value.push({ role: 'user', text: message })
  await scroll()

  const steps: TraceStep[] = []
  let answer = ''
  let provider = ''
  const sessionId = `web-${Date.now()}`
  try {
    await chatOnce(sessionId, message, (msg) => {
      if (msg.type === 'tool_trace') {
        steps.push(msg.step)
      } else if (msg.type === 'answer') {
        answer = msg.answer
        provider = msg.provider_used
      } else if (msg.type === 'error') {
        answer = `出错了：${msg.detail ?? '未知错误'}`
      }
    })
  } catch {
    answer = '连接失败：后端不可达或服务未启动'
  }
  bubbles.value.push({ role: 'assistant', text: answer, steps, provider })
  await scroll()
  busy.value = false
}

async function scroll() {
  await nextTick()
  listEl.value?.scrollTo({ top: 999999, behavior: 'smooth' })
}

function isJsonStr(s: string): boolean {
  try { JSON.parse(s); return true } catch { return false }
}
</script>

<template>
  <el-card shadow="never" class="chat-card">
    <template #header>
      <div class="head">
        <span>智能问答 · 秘书 Agent</span>
        <el-tag v-if="busy" type="warning" size="small" effect="plain">Agent 思考中…</el-tag>
      </div>
    </template>

    <div ref="listEl" class="msgs">
      <el-empty v-if="!bubbles.length" description="问点什么吧 — 例如「3号CT状态怎么样」" />
      <div v-for="(b, i) in bubbles" :key="i" class="row" :class="b.role">
        <div class="bubble">
          <div class="text" v-if="b.text">{{ b.text }}</div>
          <div class="meta" v-if="b.provider">provider: {{ b.provider }}</div>

          <el-collapse v-if="b.steps?.length" class="traces">
            <el-collapse-item :title="`工具调用轨迹（${b.steps.length}）`" name="t">
              <div v-for="(s, si) in b.steps" :key="si" class="step">
                <el-tag size="small" :type="s.ok === false ? 'danger' : 'primary'" effect="plain">
                  {{ s.tool }}
                </el-tag>
                <el-input
                  v-if="s.args && isJsonStr(String(s.args))"
                  :model-value="JSON.stringify(s.args, null, 2)" type="textarea" :rows="2" readonly class="args"
                />
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </div>
    </div>

    <div class="composer">
      <el-row :gutter="6" class="sug">
        <el-col v-for="s in suggestions" :key="s" :span="6">
          <el-button size="small" text @click="send(s)">{{ s }}</el-button>
        </el-col>
      </el-row>
      <el-input
        v-model="input" placeholder="输入问题，Enter 发送" :disabled="busy"
        @keyup.enter="send()"
      >
        <template #append>
          <el-button :disabled="busy" @click="send()">发送</el-button>
        </template>
      </el-input>
    </div>
  </el-card>
</template>

<style scoped>
.chat-card { display: flex; flex-direction: column; height: calc(100vh - 120px); }
.head { display: flex; justify-content: space-between; align-items: center; }
.msgs { flex: 1; overflow-y: auto; padding: 8px 4px; }
.row { display: flex; margin: 10px 0; }
.row.user { justify-content: flex-end; }
.bubble { max-width: 70%; padding: 10px 14px; border-radius: 8px; background: #f4f4f5; }
.row.user .bubble { background: #ecf5ff; }
.text { white-space: pre-wrap; word-break: break-word; }
.meta { font-size: 11px; color: #909399; margin-top: 4px; }
.traces { margin-top: 8px; }
.step { margin-bottom: 6px; }
.args { margin-top: 4px; }
.composer { border-top: 1px solid #e4e7ed; padding-top: 10px; }
.sug { margin-bottom: 6px; }
</style>
