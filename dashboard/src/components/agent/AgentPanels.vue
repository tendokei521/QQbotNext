<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useNotifyStore } from '@/stores/notify'

interface TaskItem {
  task_id: string
  session_id: string
  target: string
  type: 'group' | 'private'
  repeat: string
  trigger_expr: string
  content: string
  next_trigger_time: number
  fired_count: number
  active: boolean
  created_at: number
}

interface ProactiveSession {
  session_id: string
  target: string
  type: 'group' | 'private'
  enabled: boolean
  unanswered: number
  last_user_time: number | null
  next_trigger_time: number | null
  timer: '' | 'private' | 'silence'
}

const props = defineProps<{ botId: number | null }>()

const notify = useNotifyStore()
const tasks = ref<TaskItem[]>([])
const proactive = ref<ProactiveSession[]>([])
const loadingTasks = ref(false)
const loadingProactive = ref(false)

const addForm = ref({ type: 'private', target: '', trigger: '', content: '', repeat: '' })

function fmtTime(ts: number | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

async function loadTasks() {
  loadingTasks.value = true
  try {
    const res = await http.get<{ ok: boolean; tasks: TaskItem[] }>('/api/agent/tasks', {
      params: { bot_id: props.botId },
    })
    tasks.value = res.data?.tasks || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loadingTasks.value = false
  }
}

async function loadProactive() {
  loadingProactive.value = true
  try {
    const res = await http.get<{ ok: boolean; sessions: ProactiveSession[] }>('/api/agent/proactive/status', {
      params: { bot_id: props.botId },
    })
    proactive.value = res.data?.sessions || []
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loadingProactive.value = false
  }
}

function loadAll() {
  if (!props.botId) {
    tasks.value = []
    proactive.value = []
    return
  }
  loadTasks()
  loadProactive()
}

async function addTask() {
  const f = addForm.value
  if (!f.trigger.trim() || !f.content.trim() || !f.target.trim()) {
    notify.push('请填写时间表达式、目标与内容', 'warning')
    return
  }
  try {
    const res = await http.post(
      '/api/agent/tasks',
      {
        trigger: f.trigger.trim(),
        content: f.content.trim(),
        is_group: f.type === 'group',
        target: f.target.trim(),
        repeat: f.repeat.trim(),
      },      { params: { bot_id: props.botId } },
    )
    notify.push(`任务已创建${res.data?.task_id ? `（${res.data.task_id}）` : ''}`, 'success')
    addForm.value = { type: 'private', target: '', trigger: '', content: '', repeat: '' }
    await loadTasks()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function triggerTask(taskId: string) {
  try {
    await http.post(`/api/agent/tasks/${taskId}/trigger`, null, { params: { bot_id: props.botId } })
    notify.push('已立即触发任务', 'success')
    await loadTasks()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function cancelTask(taskId: string) {
  try {
    await http.post(`/api/agent/tasks/${taskId}/cancel`, null, { params: { bot_id: props.botId } })
    notify.push('已取消任务', 'success')
    await loadTasks()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function triggerProactive(sessionId: string) {
  try {
    await http.post('/api/agent/proactive/trigger', { session_id: sessionId }, { params: { bot_id: props.botId } })
    notify.push('已触发主动发言', 'success')
    await loadProactive()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

watch(() => props.botId, loadAll)
onMounted(loadAll)
</script>

<template>
  <div class="agent-panels">
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-clock-outline" class="mr-2" color="primary" /> 定时任务
        <v-spacer />
        <v-btn variant="tonal" size="small" prepend-icon="mdi-refresh" :loading="loadingTasks" @click="loadTasks">刷新任务</v-btn>
      </v-card-title>
      <v-card-text>
        <div class="add-task-row">
          <v-select
            v-model="addForm.type"
            :items="[
              { title: '私聊', value: 'private' },
              { title: '群聊', value: 'group' },
            ]"
            density="compact"
            variant="outlined"
            hide-details
            style="max-width: 100px"
          />
          <v-text-field v-model="addForm.target" label="QQ号 / 群号" density="compact" variant="outlined" hide-details style="max-width: 150px" />
          <v-text-field v-model="addForm.trigger" label="时间表达式（如：明天早上8点 / 每天中午12点）" density="compact" variant="outlined" hide-details />
          <v-text-field v-model="addForm.content" label="要发送的内容" density="compact" variant="outlined" hide-details />
          <v-text-field v-model="addForm.repeat" label="重复规则（可选）" density="compact" variant="outlined" hide-details style="max-width: 160px" />
          <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="addTask">添加</v-btn>
        </div>

        <v-table density="compact" class="mt-3">
          <thead>
            <tr>
              <th>任务ID</th>
              <th>会话</th>
              <th>重复</th>
              <th>下次触发</th>
              <th>次数</th>
              <th>内容</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="tasks.length === 0">
              <td colspan="7" class="text-center text-caption">—</td>
            </tr>
            <tr v-for="t in tasks" :key="t.task_id">
              <td class="text-caption">{{ t.task_id }}</td>
              <td class="text-caption">{{ t.session_id }}</td>
              <td class="text-caption">{{ t.repeat || '—' }}</td>
              <td class="text-caption">{{ fmtTime(t.next_trigger_time) }}</td>
              <td class="text-caption">{{ t.fired_count }}</td>
              <td class="text-caption task-content">{{ t.content }}</td>
              <td>
                <v-btn size="x-small" variant="text" color="primary" @click="triggerTask(t.task_id)">触发</v-btn>
                <v-btn size="x-small" variant="text" color="error" @click="cancelTask(t.task_id)">取消</v-btn>
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card-text>
    </v-card>

    <v-card variant="outlined">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-bullhorn-outline" class="mr-2" color="primary" /> 主动消息状态
        <v-spacer />
        <v-btn variant="tonal" size="small" prepend-icon="mdi-refresh" :loading="loadingProactive" @click="loadProactive">刷新状态</v-btn>
      </v-card-title>
      <v-card-text>
        <v-table density="compact">
          <thead>
            <tr>
              <th>会话</th>
              <th>类型</th>
              <th>启用</th>
              <th>未回复</th>
              <th>下次触发</th>
              <th>计时器</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="proactive.length === 0">
              <td colspan="7" class="text-center text-caption">—</td>
            </tr>
            <tr v-for="s in proactive" :key="s.session_id">
              <td class="text-caption">{{ s.session_id }}</td>
              <td class="text-caption">{{ s.type === 'group' ? '群聊' : '私聊' }}</td>
              <td>
                <v-chip size="x-small" :color="s.enabled ? 'success' : 'default'" variant="tonal">{{ s.enabled ? '启用' : '停用' }}</v-chip>
              </td>
              <td class="text-caption">{{ s.unanswered }}</td>
              <td class="text-caption">{{ fmtTime(s.next_trigger_time) }}</td>
              <td class="text-caption">{{ s.timer || '—' }}</td>
              <td>
                <v-btn size="x-small" variant="text" color="primary" :disabled="!s.enabled" @click="triggerProactive(s.session_id)">触发</v-btn>
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card-text>
    </v-card>
  </div>
</template>

<style scoped>
.add-task-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.task-content {
  max-width: 240px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
