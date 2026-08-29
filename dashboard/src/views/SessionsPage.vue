<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'

interface SessionSummary {
  session_id: string
  type: string
  conversation_count: number
  total_messages: number
  last_saved_at: number
}

interface ConversationSummary {
  task_id: string
  conv_id: string
  title: string
  messages: number
  saved_at: number
}

interface ConversationDetail {
  session_id: string
  type: string
  conv_id: string
  task_id: string
  title: string
  saved_at: number
  messages: HistoryMessage[]
}

interface HistoryMessage {
  role: string
  content: string
  user_id?: string
  nickname?: string
  message_id?: string
  time?: number
}

const bots = useBotsStore()
const notify = useNotifyStore()

const sessions = ref<SessionSummary[]>([])
const conversations = ref<ConversationSummary[]>([])
const conversation = ref<ConversationDetail | null>(null)

const loadingSessions = ref(false)
const loadingConversations = ref(false)
const loadingConversation = ref(false)

const selectedSessionId = ref('')
const selectedTaskId = ref('')

const botId = computed(() => bots.currentBot?.bot_id ?? null)

function typeLabel(type: string): string {
  return type === 'group' ? '群聊' : type === 'private' ? '私聊' : type || '未知'
}

function fmtTime(ts?: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

function roleLabel(role: string): string {
  if (role === 'user') return '用户'
  if (role === 'assistant') return '助手'
  if (role === 'system') return '系统'
  return role
}

function roleColor(role: string): string {
  if (role === 'user') return 'primary'
  if (role === 'assistant') return 'success'
  if (role === 'system') return 'warning'
  return 'default'
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function loadSessions() {
  if (botId.value == null) {
    sessions.value = []
    conversations.value = []
    conversation.value = null
    return
  }
  loadingSessions.value = true
  try {
    const res = await http.get<{ ok: boolean; sessions: SessionSummary[] }>('/api/sessions', {
      params: { bot_id: botId.value },
    })
    sessions.value = res.data?.sessions || []
    if (!selectedSessionId.value || !sessions.value.some((s) => s.session_id === selectedSessionId.value)) {
      selectedSessionId.value = sessions.value[0]?.session_id || ''
    }
    if (selectedSessionId.value) {
      await loadConversations()
    } else {
      conversations.value = []
      conversation.value = null
      selectedTaskId.value = ''
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loadingSessions.value = false
  }
}

async function loadConversations() {
  if (!selectedSessionId.value || botId.value == null) {
    conversations.value = []
    return
  }
  loadingConversations.value = true
  try {
    const res = await http.get<{ ok: boolean; session: { conversations: ConversationSummary[] } }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}`,
      { params: { bot_id: botId.value } },
    )
    conversations.value = res.data?.session?.conversations || []
    if (selectedTaskId.value && !conversations.value.some((c) => c.task_id === selectedTaskId.value)) {
      selectedTaskId.value = conversations.value[0]?.task_id || ''
    }
    if (!selectedTaskId.value && conversations.value.length) {
      selectedTaskId.value = conversations.value[0].task_id
    }
    if (selectedTaskId.value) {
      await openConversation(selectedTaskId.value)
    } else {
      conversation.value = null
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loadingConversations.value = false
  }
}

async function openConversation(taskId: string) {
  if (!selectedSessionId.value || botId.value == null) return
  selectedTaskId.value = taskId
  loadingConversation.value = true
  try {
    const res = await http.get<{ ok: boolean; conversation: ConversationDetail }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${taskId}`,
      { params: { bot_id: botId.value } },
    )
    conversation.value = res.data?.conversation || null
  } catch (err) {
    notify.push(errorMessage(err), 'error')
    conversation.value = null
  } finally {
    loadingConversation.value = false
  }
}

function selectSession(id: string) {
  selectedSessionId.value = id
  selectedTaskId.value = ''
  conversation.value = null
  loadConversations()
}

async function renameConversation(target: ConversationSummary) {
  const title = window.prompt('新的对话标题', target.title)
  if (title == null) return
  if (!title.trim()) {
    notify.push('标题不能为空', 'warning')
    return
  }
  try {
    const res = await http.put<{ ok: boolean; conversation: ConversationDetail }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${target.task_id}/rename`,
      { title: title.trim() },
      { params: { bot_id: botId.value } },
    )
    target.title = res.data?.conversation?.title || title.trim()
    if (conversation.value?.task_id === target.task_id) {
      conversation.value.title = target.title
    }
    notify.push('对话已重命名', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function deleteConversation(target: ConversationSummary) {
  if (!window.confirm(`确认删除对话「${target.title}」？该操作不可恢复。`)) return
  try {
    await http.delete(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${target.task_id}`,
      { params: { bot_id: botId.value } },
    )
    notify.push('对话已删除', 'success')
    if (selectedTaskId.value === target.task_id) {
      conversation.value = null
      selectedTaskId.value = ''
    }
    await loadConversations()
    await loadSessions()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function deleteMessage(index: number) {
  if (!conversation.value || !window.confirm(`确认删除第 ${index + 1} 条消息？`)) return
  try {
    const res = await http.delete<{ ok: boolean; conversation: ConversationDetail }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${conversation.value.task_id}/messages/${index}`,
      { params: { bot_id: botId.value } },
    )
    conversation.value = res.data?.conversation || conversation.value
    notify.push('消息已删除', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function exportConversation(format: 'text' | 'json') {
  if (!conversation.value || botId.value == null) return
  try {
    const res = await http.get(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${conversation.value.task_id}/export`,
      { params: { bot_id: botId.value, format }, responseType: 'blob' },
    )
    const blob = res.data instanceof Blob ? res.data : new Blob([res.data])
    const filename = format === 'json'
      ? `${conversation.value.task_id}.json`
      : `${conversation.value.task_id}.txt`
    saveBlob(blob, filename)
    notify.push('导出已开始', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

watch(botId, () => {
  selectedSessionId.value = ''
  selectedTaskId.value = ''
  conversation.value = null
  loadSessions()
})

onMounted(() => {
  loadSessions()
})
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">会话数据</h1>
        <div class="app-page-subtitle">查看、编辑和导出当前账号的本地会话历史</div>
      </div>
      <v-spacer />
      <v-btn variant="tonal" prepend-icon="mdi-refresh" @click="loadSessions">刷新</v-btn>
    </div>

    <v-row class="mt-2">
      <v-col cols="12" md="3">
        <v-card variant="outlined" class="h-full">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-chat-outline" class="mr-2" color="primary" /> 会话列表
          </v-card-title>
          <v-card-text class="pa-2">
            <v-progress-linear v-if="loadingSessions" indeterminate color="primary" />
            <template v-else-if="botId == null">
              <div class="text-caption text-center pa-4">请先连接账号以获取 bot_id</div>
            </template>
            <template v-else-if="sessions.length === 0">
              <div class="text-caption text-center pa-4">暂无会话数据</div>
            </template>
            <v-list v-else density="compact">
              <v-list-item
                v-for="s in sessions"
                :key="s.session_id"
                :active="selectedSessionId === s.session_id"
                @click="selectSession(s.session_id)"
              >
                <template #prepend>
                  <v-icon :icon="s.type === 'group' ? 'mdi-account-group' : 'mdi-account'" color="primary" />
                </template>
                <v-list-item-title class="text-body-2">{{ s.session_id }}</v-list-item-title>
                <v-list-item-subtitle class="text-caption">
                  {{ typeLabel(s.type) }} · {{ s.conversation_count }} 个对话 · {{ s.total_messages }} 条消息
                </v-list-item-subtitle>
                <template #append>
                  <span class="text-caption" style="opacity: 0.55">{{ fmtTime(s.last_saved_at) }}</span>
                </template>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="3">
        <v-card variant="outlined" class="h-full">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-message-text-outline" class="mr-2" color="primary" /> 对话线程
          </v-card-title>
          <v-card-text class="pa-2">
            <v-progress-linear v-if="loadingConversations" indeterminate color="primary" />
            <template v-else-if="!selectedSessionId">
              <div class="text-caption text-center pa-4">请选择左侧会话</div>
            </template>
            <template v-else-if="conversations.length === 0">
              <div class="text-caption text-center pa-4">该会话暂无对话</div>
            </template>
            <v-list v-else density="compact">
              <v-list-item
                v-for="c in conversations"
                :key="c.task_id"
                :active="selectedTaskId === c.task_id"
                @click="openConversation(c.task_id)"
              >
                <v-list-item-title class="text-body-2">{{ c.title }}</v-list-item-title>
                <v-list-item-subtitle class="text-caption">
                  {{ c.messages }} 条消息 · {{ fmtTime(c.saved_at) }}
                </v-list-item-subtitle>
                <template #append>
                  <v-btn size="x-small" variant="text" icon="mdi-pencil" title="重命名" @click.stop="renameConversation(c)" />
                  <v-btn size="x-small" variant="text" icon="mdi-delete" color="error" title="删除对话" @click.stop="deleteConversation(c)" />
                </template>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6">
        <v-card variant="outlined" class="h-full">
          <v-card-title v-if="conversation" class="d-flex align-center flex-wrap gap-2">
            <v-icon icon="mdi-message-text" class="mr-1" color="primary" />
            <span class="text-truncate">{{ conversation.title }}</span>
            <v-chip size="x-small" variant="tonal" class="ml-1">{{ conversation.task_id }}</v-chip>
            <v-spacer />
            <v-btn size="small" variant="tonal" prepend-icon="mdi-download" @click="exportConversation('text')">导出 TXT</v-btn>
            <v-btn size="small" variant="tonal" prepend-icon="mdi-json" @click="exportConversation('json')">导出 JSON</v-btn>
          </v-card-title>
          <v-card-title v-else class="text-caption">请选择对话线程查看消息</v-card-title>

          <v-card-text class="pa-2">
            <v-progress-linear v-if="loadingConversation" indeterminate color="primary" />
            <template v-else-if="!conversation">
              <div class="text-caption text-center pa-6">暂无消息内容</div>
            </template>
            <div v-else class="session-messages" style="max-height: calc(100vh - 260px); overflow-y: auto">
              <div v-for="(msg, index) in conversation.messages" :key="index" class="message-row">
                <div class="message-meta">
                  <v-chip size="x-small" :color="roleColor(msg.role)" variant="tonal">{{ roleLabel(msg.role) }}</v-chip>
                  <span v-if="msg.role === 'user'" class="text-caption">
                    {{ msg.nickname ? `${msg.nickname}(${msg.user_id || '?'})` : `QQ ${msg.user_id || '?'}` }}
                  </span>
                  <span v-if="msg.message_id" class="text-caption" style="opacity: 0.55">消息 {{ msg.message_id }}</span>
                  <span class="text-caption" style="opacity: 0.55">{{ fmtTime(msg.time) }}</span>
                  <v-spacer />
                  <v-btn
                    size="x-small"
                    variant="text"
                    icon="mdi-delete"
                    color="error"
                    title="删除消息"
                    @click="deleteMessage(index)"
                  />
                </div>
                <div class="message-content">{{ msg.content }}</div>
              </div>
              <div v-if="!conversation.messages.length" class="text-caption text-center pa-6">该对话没有消息</div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<style scoped>
.h-full {
  height: 100%;
}

.session-messages {
  padding: 4px 2px;
}

.message-row {
  padding: 10px 10px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 8px;
}

.message-row:hover {
  background: rgba(var(--v-theme-primary), 0.04);
}

.message-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.message-content {
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
