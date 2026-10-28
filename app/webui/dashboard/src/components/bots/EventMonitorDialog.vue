<script setup lang="ts">
/**
 * 诊断台：查看某个账号最近收到的 OneBot 事件，并可发一条测试消息。
 *
 * 解决最高频的求助场景「连上了但 @ 没反应」：先看协议端到底有没有把事件送进来，
 * 再决定是协议端问题还是机器人的回复配置问题。
 */
import { computed, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useNotifyStore } from '@/stores/notify'
import EmptyState from '@/components/EmptyState.vue'
import { cleanLogTimestamp } from '@/utils/log'

const props = defineProps<{
  modelValue: boolean
  accountId: string | null
  accountName?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const notify = useNotifyStore()

interface OnebotEvent {
  seq: number
  time?: number | string
  event_type?: string
  user_id?: string | number | null
  group_id?: string | number | null
  summary?: string
  raw?: unknown
}

const events = ref<OnebotEvent[]>([])
const connected = ref(false)
const listening = ref(false)
const loading = ref(false)
const loadError = ref('')
const detail = ref<OnebotEvent | null>(null)

// 测试消息
const groupId = ref('')
const testText = ref('')
const sending = ref(false)

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

let timer: ReturnType<typeof setInterval> | null = null

function stopPolling() {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

async function load() {
  if (!props.accountId) return
  loading.value = true
  try {
    const res = await http.get(`/api/bots/${encodeURIComponent(props.accountId)}/events`, {
      params: { limit: 50 },
    })
    events.value = Array.isArray(res.data?.events) ? res.data.events : []
    connected.value = Boolean(res.data?.connected)
    listening.value = Boolean(res.data?.listening)
    loadError.value = ''
  } catch (err) {
    loadError.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function clearEvents() {
  if (!props.accountId) return
  try {
    await http.post(`/api/bots/${encodeURIComponent(props.accountId)}/events/clear`)
    events.value = []
    notify.push('已清空事件记录', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function latestGroupId(): string {
  for (let i = events.value.length - 1; i >= 0; i -= 1) {
    const gid = events.value[i]?.group_id
    if (gid) return String(gid)
  }
  return ''
}

function useLatestGroup() {
  const gid = latestGroupId()
  if (!gid) {
    notify.push('还没有收到群消息，先手动填群号', 'warning')
    return
  }
  groupId.value = gid
}

async function sendTest() {
  if (!props.accountId) return
  const text = testText.value.trim()
  if (!text) {
    notify.push('先写一句要发送的内容', 'warning')
    return
  }
  sending.value = true
  try {
    const body: Record<string, unknown> = { text }
    const gid = groupId.value.trim()
    if (gid) {
      const parsed = Number(gid)
      if (!Number.isInteger(parsed) || parsed <= 0) {
        notify.push('群号要填数字', 'warning')
        return
      }
      body.group_id = parsed
    }
    const res = await http.post(
      `/api/bots/${encodeURIComponent(props.accountId)}/test-message`,
      body,
    )
    notify.push(res.data?.message || '测试消息已发送', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    sending.value = false
  }
}

function eventTypeText(type?: string): string {
  const value = String(type || '')
  if (value.includes('message')) return '消息'
  if (value.includes('notice')) return '通知'
  if (value.includes('request')) return '请求'
  if (value.includes('meta')) return '心跳/元事件'
  return value || '事件'
}

function eventTypeColor(type?: string): string {
  const value = String(type || '')
  if (value.includes('message')) return 'primary'
  if (value.includes('notice')) return 'info'
  if (value.includes('request')) return 'warning'
  return 'grey'
}

function formatTime(value?: number | string): string {
  if (value === undefined || value === null || value === '') return ''
  if (typeof value === 'number') {
    const ms = value > 1e12 ? value : value * 1000
    return new Date(ms).toLocaleTimeString()
  }
  return cleanLogTimestamp(String(value))
}

function formatRaw(raw: unknown): string {
  try {
    return JSON.stringify(raw, null, 2)
  } catch {
    return String(raw)
  }
}

watch(
  () => [props.modelValue, props.accountId] as const,
  ([isOpen, accountId]) => {
    stopPolling()
    if (!isOpen || !accountId) return
    groupId.value = ''
    testText.value = ''
    detail.value = null
    void load()
    timer = setInterval(() => void load(), 2000)
  },
  { immediate: true },
)

// 关闭对话框时停轮询（watch 已覆盖，双保险）
watch(open, (v) => {
  if (!v) stopPolling()
})
</script>

<template>
  <v-dialog v-model="open" max-width="820" scrollable>
    <v-card>
      <v-toolbar density="comfortable" color="transparent">
        <v-toolbar-title class="text-body-1 font-weight-bold">
          <v-icon icon="mdi-stethoscope" class="mr-2" color="primary" />
          诊断 · {{ accountName || accountId }}
        </v-toolbar-title>
        <v-chip
          size="small"
          :color="connected ? 'success' : listening ? 'warning' : 'error'"
          variant="tonal"
          class="mr-2"
        >
          {{ connected ? '已连接' : listening ? '等待协议端接入' : '未连接' }}
        </v-chip>
        <v-btn icon="mdi-close" variant="text" size="small" @click="open = false" />
      </v-toolbar>

      <v-card-text style="max-height: 62vh">
        <div class="text-caption mb-3" style="opacity: 0.7">
          这里显示协议端最近发来的原始事件。在群里发一条消息，如果这里出现了「消息」，说明协议端已通；
          如果一直没有，问题在协议端（NapCat/LLOneBot）那一侧。
        </div>

        <v-alert v-if="loadError" type="error" variant="tonal" density="compact" class="mb-3">
          {{ loadError }}
          <v-btn size="small" variant="text" @click="load">重试</v-btn>
        </v-alert>

        <div class="d-flex align-center flex-wrap gap-2 mb-2">
          <span class="text-caption">最近 {{ events.length }} 条</span>
          <v-spacer />
          <v-btn size="small" variant="text" prepend-icon="mdi-refresh" :loading="loading" @click="load">刷新</v-btn>
          <v-btn size="small" variant="text" color="error" prepend-icon="mdi-delete-outline" :disabled="!events.length" @click="clearEvents">
            清空
          </v-btn>
        </div>

        <EmptyState
          v-if="!events.length && !loading"
          icon="mdi-radar"
          title="还没有收到事件"
          description="确认账号已连接、协议端已启动，然后在群里发一条消息"
        >
          <v-btn size="small" variant="tonal" @click="load">再看一次</v-btn>
        </EmptyState>

        <v-list v-else density="compact" class="event-list">
          <v-list-item v-for="item in [...events].reverse()" :key="item.seq" class="event-row" @click="detail = item">
            <template #prepend>
              <v-chip size="x-small" :color="eventTypeColor(item.event_type)" variant="tonal">
                {{ eventTypeText(item.event_type) }}
              </v-chip>
            </template>
            <v-list-item-title class="event-title">
              {{ item.summary || '（无文本内容）' }}
            </v-list-item-title>
            <v-list-item-subtitle class="text-caption">
              {{ formatTime(item.time) }}
              <template v-if="item.group_id"> · 群 {{ item.group_id }}</template>
              <template v-if="item.user_id"> · QQ {{ item.user_id }}</template>
            </v-list-item-subtitle>
            <template #append>
              <v-icon icon="mdi-chevron-right" size="small" />
            </template>
          </v-list-item>
        </v-list>
      </v-card-text>

      <v-divider />

      <v-card-text>
        <div class="text-subtitle-2 mb-2">发一条测试消息</div>
        <div class="d-flex flex-wrap gap-2">
          <v-text-field
            v-model="groupId"
            label="群号"
            placeholder="留空则用最近一条群消息的群"
            density="compact"
            variant="outlined"
            hide-details
            class="test-group"
          />
          <v-btn variant="tonal" size="small" class="mt-1" @click="useLatestGroup">用最近的群</v-btn>
        </div>
        <div class="d-flex flex-wrap gap-2 mt-2">
          <v-text-field
            v-model="testText"
            label="内容"
            placeholder="例如：测试消息，收到请忽略"
            density="compact"
            variant="outlined"
            hide-details
            maxlength="500"
            counter
            class="test-text"
            @keyup.enter="sendTest"
          />
          <v-btn color="primary" variant="tonal" :loading="sending" prepend-icon="mdi-send" class="mt-1" @click="sendTest">
            发送
          </v-btn>
        </div>
        <div class="text-caption mt-2" style="opacity: 0.6">
          消息会以当前账号的身份发到指定群；发送失败时这里会显示协议端返回的原因。
        </div>
      </v-card-text>
    </v-card>

    <v-dialog :model-value="detail !== null" max-width="640" scrollable @update:model-value="(v: boolean) => { if (!v) detail = null }">
      <v-card v-if="detail">
        <v-card-title class="text-body-2">
          事件 #{{ detail.seq }} · {{ eventTypeText(detail.event_type) }}
        </v-card-title>
        <v-card-text>
          <pre class="raw-json">{{ formatRaw(detail.raw) }}</pre>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="detail = null">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-dialog>
</template>

<style scoped>
.event-list {
  max-height: 40vh;
  overflow-y: auto;
}

.event-row {
  border-radius: 10px;
  cursor: pointer;
}

.event-row:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.event-title {
  font-size: 13px;
  white-space: normal;
  word-break: break-all;
}

.raw-json {
  max-height: 50vh;
  overflow: auto;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 8px;
  padding: 10px;
  font-size: 12px;
  line-height: 1.5;
}

.test-group {
  width: 240px;
}

.test-text {
  flex: 1 1 auto;
  min-width: 240px;
}
</style>
