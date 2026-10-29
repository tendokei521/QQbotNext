<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AxiosError } from 'axios'
import http, { errorMessage, token } from '@/api/http'
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

/** 一条消息里的图片：`seg` 是段下标（换/删要回传给后端），`url` 是本地图片地址 */
interface HistoryImage {
  seg: number
  sha: string
  mime: string
  bytes: number
  status: 'cached' | 'missing' | 'uncached'
  url: string
}

interface HistoryMessage {
  role: string
  content: string
  user_id?: string
  nickname?: string
  message_id?: string
  time?: number
  images?: HistoryImage[]
}

interface MediaStats {
  count: number
  bytes: number
  referenced: number
  unreferenced: number
  files?: { name: string; sha: string; mime: string; bytes: number; referenced: boolean }[]
}

const bots = useBotsStore()
const notify = useNotifyStore()
const route = useRoute()
const router = useRouter()

const sessions = ref<SessionSummary[]>([])
const conversations = ref<ConversationSummary[]>([])
const conversation = ref<ConversationDetail | null>(null)

const loadingSessions = ref(false)
const loadingConversations = ref(false)
const loadingConversation = ref(false)

/** 加载失败原因（空串 = 正常）：失败时必须显示错误态，不能伪装成「暂无数据」 */
const sessionsError = ref('')
const conversationsError = ref('')

// 请求序号：快速切换会话/对话（或切账号）时丢弃过期响应，避免标题与内容错配
let sessionsSeq = 0
let listSeq = 0
let conversationSeq = 0

const selectedSessionId = ref('')
const selectedTaskId = ref('')
const selectedTaskIds = ref<string[]>([])

/** 列表搜索：记录多了以后靠滚动找不动 */
const sessionQuery = ref('')
const conversationQuery = ref('')
/** 消息区容器：用于「跳到最新」 */
const messageBodyEl = ref<HTMLDivElement | null>(null)

const editDialog = ref(false)
const editingIndex = ref(-1)
const editContent = ref('')

const deletingTaskId = ref('')
const bulkDeleting = ref(false)

const newMessageRole = ref<'user' | 'assistant' | 'system'>('user')
const newMessageContent = ref('')
const restoreFileInput = ref<HTMLInputElement | null>(null)
const mediaFileInput = ref<HTMLInputElement | null>(null)

/** 本地图片仓（每账号一个 media/ 目录）的占用情况 */
const mediaStats = ref<MediaStats | null>(null)
const loadingMedia = ref(false)
const purgingMedia = ref(false)

/** 图片预览 / 换图的目标 */
const previewImage = ref<HistoryImage | null>(null)
const imageBusy = ref('')
const pendingImageTarget = ref<{ index: number; image: HistoryImage } | null>(null)

const botId = computed(() => bots.currentAccountId)

/** 当前选中的会话（展示标题 / 删除确认用，避免直接显示内部 id） */
const selectedSession = computed(
  () => sessions.value.find((s) => s.session_id === selectedSessionId.value) || null,
)

/** 会话列表搜索：按展示标题或会话标识匹配 */
const filteredSessions = computed(() => {
  const keyword = sessionQuery.value.trim().toLowerCase()
  if (!keyword) return sessions.value
  return sessions.value.filter(
    (s) =>
      sessionTitle(s).toLowerCase().includes(keyword) ||
      String(s.session_id || '').toLowerCase().includes(keyword),
  )
})

/** 对话列表搜索：按标题或任务标识匹配 */
const filteredConversations = computed(() => {
  const keyword = conversationQuery.value.trim().toLowerCase()
  if (!keyword) return conversations.value
  return conversations.value.filter(
    (c) =>
      String(c.title || '').toLowerCase().includes(keyword) ||
      String(c.task_id || '').toLowerCase().includes(keyword),
  )
})

function isNotFoundError(err: unknown): boolean {
  return err instanceof AxiosError && err.response?.status === 404
}

function fmtBytes(bytes?: number): string {
  const n = Number(bytes || 0)
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/**
 * 图片地址要带上 account_id 与 token：`<img>` 发不了 Authorization 头，
 * 所以走 `?token=`（服务端鉴权中间件认这个参数）。
 */
function imageSrc(image: HistoryImage): string {
  if (!image?.url || botId.value == null) return ''
  const params = new URLSearchParams({ account_id: String(botId.value) })
  const t = token.get()
  if (t) params.set('token', t)
  return `${image.url}?${params.toString()}`
}

function typeLabel(type: string): string {
  return type === 'group' ? '群聊' : type === 'private' ? '私聊' : type || '未知'
}

/**
 * 会话标题：内部 session_id（如 group_123456）对用户没有意义，
 * 转成「群 123456」「私聊 QQ 789」；确实没有可读数字时退回类型名，不把内部 id 当标题展示。
 */
function sessionTitle(s: SessionSummary): string {
  const numeric = /(\d{4,})\s*$/.exec(String(s.session_id || ''))?.[1] || ''
  if (numeric) {
    if (s.type === 'group') return `群 ${numeric}`
    if (s.type === 'private') return `私聊 QQ ${numeric}`
    return `会话 ${numeric}`
  }
  if (s.type === 'group') return '群聊会话'
  if (s.type === 'private') return '私聊会话'
  return '会话'
}

/** 导出文件名里去掉系统不允许的字符 */
function safeFileName(name: string): string {
  return String(name || '').replace(/[\\/:*?"<>|]/g, '_').trim() || '会话'
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
    sessionsError.value = ''
    return
  }
  loadingSessions.value = true
  sessionsError.value = ''
  const seq = ++sessionsSeq
  const accountId = botId.value
  const stillCurrent = () => seq === sessionsSeq && accountId === botId.value
  try {
    const res = await http.get<{ ok: boolean; sessions: SessionSummary[] }>('/api/sessions', {
      params: { account_id: accountId },
    })
    if (!stillCurrent()) return
    sessions.value = res.data?.sessions || []
    // 外部链接（数据统计的「查看会话」）带 ?session= 时优先选中它
    const wanted = querySessionId()
    const preferred =
      wanted && sessions.value.some((s) => s.session_id === wanted) ? wanted : ''
    // 外部链接指定的会话不在当前账号下（地址过期 / 别的账号）：清掉地址参数，别让它一直指着不存在的会话
    if (wanted && !preferred) syncSessionQuery('')
    if (preferred) {
      if (selectedSessionId.value !== preferred) {
        selectedSessionId.value = preferred
        selectedTaskId.value = ''
        conversation.value = null
      }
    } else if (
      !selectedSessionId.value ||
      !sessions.value.some((s) => s.session_id === selectedSessionId.value)
    ) {
      selectedSessionId.value = sessions.value[0]?.session_id || ''
      selectedTaskId.value = '' // 换了会话：旧对话 id 不再有效
      conversation.value = null
    }
    if (selectedSessionId.value) {
      await loadConversations()
    } else {
      conversations.value = []
      conversation.value = null
      selectedTaskId.value = ''
      selectedTaskIds.value = []
      syncSessionQuery('')
    }
  } catch (err) {
    if (!stillCurrent()) return
    // 失败时保留错误原因给页面显示重试入口，而不是让列表表现为「没有会话」
    sessionsError.value = errorMessage(err)
    notify.push(errorMessage(err), 'error')
  } finally {
    if (seq === sessionsSeq) loadingSessions.value = false
  }
  await loadMediaStats()
}

/**
 * 拉当前会话的对话列表。
 *
 * `autoOpen=false` 用于「刚删完东西」：不要再替用户自动打开第一个对话——
 * 以前删掉唯一对话后，这里会立刻去打开一个已经不存在的对话，界面于是弹出
 * 「该会话不存在 / 对话不存在」。删完就停在列表上，用户点哪个打开哪个。
 */
async function loadConversations(autoOpen = true) {
  if (!selectedSessionId.value || botId.value == null) {
    conversations.value = []
    conversationsError.value = ''
    return
  }
  const seq = ++listSeq
  const sessionId = selectedSessionId.value
  const accountId = botId.value
  /** 本次响应是否仍对应「当前选中的会话/账号」 */
  const stillCurrent = () =>
    seq === listSeq && sessionId === selectedSessionId.value && accountId === botId.value
  loadingConversations.value = true
  conversationsError.value = ''
  try {
    const res = await http.get<{ ok: boolean; session: { conversations: ConversationSummary[]; empty?: boolean } }>(
      `/api/sessions/${encodeURIComponent(sessionId)}`,
      { params: { account_id: accountId } },
    )
    if (!stillCurrent()) return
    conversations.value = res.data?.session?.conversations || []
    selectedTaskIds.value = selectedTaskIds.value.filter((id) =>
      conversations.value.some((c) => c.task_id === id),
    )
    if (selectedTaskId.value && !conversations.value.some((c) => c.task_id === selectedTaskId.value)) {
      selectedTaskId.value = ''
      conversation.value = null
    }
    if (selectedTaskId.value) {
      await openConversation(selectedTaskId.value)
    } else if (autoOpen && conversations.value.length) {
      await openConversation(conversations.value[0].task_id)
    } else {
      conversation.value = null
    }
  } catch (err) {
    if (!stillCurrent()) return
    if (isNotFoundError(err)) {
      // 会话在这期间被删掉了：列表归零，不弹错误（这不是用户操作失败）
      conversations.value = []
      selectedTaskId.value = ''
      conversation.value = null
      return
    }
    conversationsError.value = errorMessage(err)
    notify.push(errorMessage(err), 'error')
  } finally {
    if (seq === listSeq) loadingConversations.value = false
  }
}

/**
 * 外部链接通过 ?session= 指定要打开的会话（数据统计的「查看会话」用）。
 * 只做选中；具体对话由 loadConversations 去拉。
 */
function querySessionId(): string {
  return String(route.query.session || '')
}

/** 把当前选中会话写回地址栏：刷新/分享后仍停在同一个会话 */
function syncSessionQuery(sessionId: string) {
  if (String(route.query.session || '') === sessionId) return
  const query = { ...route.query }
  if (sessionId) query.session = sessionId
  else delete query.session
  void router.replace({ query })
}

function applyQuerySelection() {
  const wanted = querySessionId()
  if (!wanted || wanted === selectedSessionId.value) return
  if (!sessions.value.some((s) => s.session_id === wanted)) {
    // 链接指向的会话不在当前账号下：清掉参数并说明，避免界面停在旧选中项却对不上地址
    syncSessionQuery('')
    notify.push('这个会话不在当前账号下，已切换回列表', 'warning')
    return
  }
  selectedSessionId.value = wanted
  selectedTaskId.value = ''
  conversation.value = null
  // 作废在途的旧会话请求
  conversationSeq += 1
  loadingConversation.value = false
  void loadConversations()
}

async function loadMediaStats() {
  if (botId.value == null) {
    mediaStats.value = null
    return
  }
  loadingMedia.value = true
  try {
    const res = await http.get<MediaStats & { ok: boolean }>('/api/media', {
      params: { account_id: botId.value },
    })
    mediaStats.value = res.data || null
  } catch (err) {
    mediaStats.value = null
  } finally {
    loadingMedia.value = false
  }
}

async function openConversation(taskId: string) {
  if (!selectedSessionId.value || botId.value == null) return
  const seq = ++conversationSeq
  const sessionId = selectedSessionId.value
  const accountId = botId.value
  /** 本次响应是否仍对应「当前选中的对话/会话/账号」 */
  const stillCurrent = () =>
    seq === conversationSeq && sessionId === selectedSessionId.value && accountId === botId.value
  selectedTaskId.value = taskId
  loadingConversation.value = true
  try {
    const res = await http.get<{ ok: boolean; conversation: ConversationDetail }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/conversations/${taskId}`,
      { params: { account_id: accountId } },
    )
    if (!stillCurrent()) return
    conversation.value = res.data?.conversation || null
  } catch (err) {
    if (!stillCurrent()) return
    conversation.value = null
    if (isNotFoundError(err)) {
      // 对话已被删除（多端操作 / 刚归档）：静默把选中项清掉，别再弹「不存在」
      selectedTaskId.value = ''
      await loadConversations(false)
      return
    }
    notify.push(errorMessage(err), 'error')
  } finally {
    if (seq === conversationSeq) loadingConversation.value = false
  }
}

function selectSession(id: string) {
  selectedSessionId.value = id
  selectedTaskId.value = ''
  selectedTaskIds.value = []
  conversation.value = null
  // 切换会话：作废在途的详情请求，避免旧会话内容盖到新选择上
  conversationSeq += 1
  loadingConversation.value = false
  syncSessionQuery(id)
  loadConversations()
}

function toggleTaskSelection(taskId: string) {
  if (selectedTaskIds.value.includes(taskId)) {
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => id !== taskId)
  } else {
    selectedTaskIds.value.push(taskId)
  }
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
      { params: { account_id: botId.value } },
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
  if (deletingTaskId.value) return
  if (!window.confirm(`确认删除对话「${target.title}」？该操作不可恢复。`)) return
  deletingTaskId.value = target.task_id
  try {
    const res = await http.delete<{ ok: boolean; mode?: string; message?: string }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${target.task_id}`,
      { params: { account_id: botId.value } },
    )
    notify.push(res.data?.message || '对话已删除', 'success')
    if (selectedTaskId.value === target.task_id) {
      conversation.value = null
      selectedTaskId.value = ''
    }
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => id !== target.task_id)
    await loadSessions()
    await loadConversations(false) // 删完不自动打开：「列表」才是此刻该停的地方
    await loadMediaStats()
  } catch (err) {
    // 对话可能已经被删除（重复点击/多端操作），按成功处理并刷新列表
    if (isNotFoundError(err)) {
      notify.push('对话已不存在，已刷新列表', 'info')
      if (selectedTaskId.value === target.task_id) {
        conversation.value = null
        selectedTaskId.value = ''
      }
      selectedTaskIds.value = selectedTaskIds.value.filter((id) => id !== target.task_id)
      await loadSessions()
      await loadConversations(false)
      return
    }
    notify.push(errorMessage(err), 'error')
  } finally {
    deletingTaskId.value = ''
  }
}

async function bulkDeleteConversations() {
  if (!selectedTaskIds.value.length) return
  if (bulkDeleting.value) return
  if (!window.confirm(`确认删除选中的 ${selectedTaskIds.value.length} 个对话？`)) return
  bulkDeleting.value = true
  try {
    const res = await http.post<{ ok: boolean; deleted?: string[]; failed?: { task_id: string; error: string }[] }>(
      '/api/sessions/bulk-delete',
      { session_id: selectedSessionId.value, task_ids: selectedTaskIds.value },
      { params: { account_id: botId.value } },
    )
    const failed = res.data?.failed || []
    if (failed.length) {
      notify.push(`批量删除完成，${failed.length} 个对话可能已被删除或不存在`, 'warning')
    } else {
      notify.push('批量删除完成', 'success')
    }
    selectedTaskIds.value = []
    conversation.value = null
    selectedTaskId.value = ''
    await loadSessions()
    await loadConversations(false)
    await loadMediaStats()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    bulkDeleting.value = false
  }
}

async function deleteSession() {
  if (!selectedSessionId.value) return
  const label = selectedSession.value ? sessionTitle(selectedSession.value) : '该会话'
  if (!window.confirm(`确认删除会话「${label}」？该操作不可恢复。`)) return
  try {
    await http.delete(`/api/sessions/${encodeURIComponent(selectedSessionId.value)}`, {
      params: { account_id: botId.value },
    })
    notify.push('会话已删除', 'success')
    selectedSessionId.value = ''
    syncSessionQuery('')
    selectedTaskId.value = ''
    selectedTaskIds.value = []
    conversation.value = null
    await loadSessions()
    await loadMediaStats()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function exportSession() {
  if (!selectedSessionId.value || botId.value == null) return
  try {
    const res = await http.get(`/api/sessions/${encodeURIComponent(selectedSessionId.value)}/export`, {
      params: { account_id: botId.value },
      responseType: 'blob',
    })
    const blob = res.data instanceof Blob ? res.data : new Blob([res.data])
    // 备份包含 session.json + media/（图片字节），所以是 zip
    const label = selectedSession.value ? sessionTitle(selectedSession.value) : '会话'
    saveBlob(blob, `${safeFileName(label)}-备份.zip`)
    notify.push('会话备份已开始（含图片）', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function openRestorePicker() {
  restoreFileInput.value?.click()
}

function onRestoreFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const isZip = /\.zip$/i.test(file.name) || file.type === 'application/zip'
  const finish = (err?: unknown) => {
    input.value = ''
    if (err) notify.push(errorMessage(err), 'error')
  }
  if (!isZip) {
    // 兼容旧的纯 JSON 备份
    const reader = new FileReader()
    reader.onload = async () => {
      try {
        const payload = JSON.parse(String(reader.result || '{}'))
        const res = await http.post<{ ok: boolean; restored?: number; session_id?: string }>(
          '/api/sessions/restore',
          payload,
          { params: { account_id: botId.value } },
        )
        notify.push(`恢复完成，导入 ${res.data?.restored ?? '?'} 个对话`, 'success')
        selectedSessionId.value = res.data?.session_id || selectedSessionId.value
        selectedTaskId.value = ''
        conversation.value = null
        await loadSessions()
        await loadMediaStats()
        finish()
      } catch (err) {
        finish(err)
      }
    }
    reader.readAsText(file)
    return
  }
  // zip 备份：图片会一起落盘，指向自然可用
  file
    .arrayBuffer()
    .then((buffer) =>
      http.post<{ ok: boolean; restored?: number; media?: number }>(
        '/api/sessions/restore-bundle',
        buffer,
        {
          params: { account_id: botId.value },
          headers: { 'Content-Type': 'application/zip' },
        },
      ),
    )
    .then(async (res) => {
      notify.push(
        `恢复完成：${res.data?.restored ?? 0} 个对话、${res.data?.media ?? 0} 张图片`,
        'success',
      )
      selectedTaskId.value = ''
      conversation.value = null
      await loadSessions()
      await loadMediaStats()
      finish()
    })
    .catch(finish)
}

// ---------- 会话里的图片：预览 / 换图 / 移除 ----------

function openImagePreview(image: HistoryImage) {
  if (image.status !== 'cached') return
  previewImage.value = image
}

function pickReplacement(index: number, image: HistoryImage) {
  pendingImageTarget.value = { index, image }
  mediaFileInput.value?.click()
}

async function onReplacementPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  const target = pendingImageTarget.value
  input.value = ''
  pendingImageTarget.value = null
  if (!file || !target || !conversation.value) return
  const key = `replace-${target.index}-${target.image.seg}`
  imageBusy.value = key
  try {
    const res = await http.put<{ ok: boolean; conversation: ConversationDetail }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${conversation.value.task_id}/messages/${target.index}/media/${target.image.seg}`,
      file,
      {
        params: { account_id: botId.value, mime: file.type || '' },
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
      },
    )
    conversation.value = res.data?.conversation || conversation.value
    notify.push('图片已替换（模型下一轮就用这张新图）', 'success')
    await loadMediaStats()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    imageBusy.value = ''
  }
}

async function removeImage(index: number, image: HistoryImage) {
  if (!conversation.value) return
  if (!window.confirm('确认从这条消息里移除这张图片？移除后模型不再看到它。')) return
  const key = `remove-${index}-${image.seg}`
  imageBusy.value = key
  try {
    const res = await http.delete<{ ok: boolean; conversation: ConversationDetail }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${conversation.value.task_id}/messages/${index}/media/${image.seg}`,
      { params: { account_id: botId.value } },
    )
    conversation.value = res.data?.conversation || conversation.value
    notify.push('图片已移除', 'success')
    await loadMediaStats()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    imageBusy.value = ''
  }
}

async function purgeMedia() {
  if (botId.value == null || purgingMedia.value) return
  if (!window.confirm('清理所有"没有任何会话引用"的本地图片？被引用的图片不会被删。')) return
  purgingMedia.value = true
  try {
    const res = await http.post<{ ok: boolean; removed?: number; kept_referenced?: number; message?: string }>(
      '/api/media/purge',
      {},
      { params: { account_id: botId.value } },
    )
    notify.push(res.data?.message || `已清理 ${res.data?.removed ?? 0} 张图片`, 'success')
    await loadMediaStats()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    purgingMedia.value = false
  }
}

function openEditMessage(index: number) {
  if (!conversation.value) return
  editingIndex.value = index
  editContent.value = conversation.value.messages[index]?.content || ''
  editDialog.value = true
}

async function saveEditMessage() {
  if (editingIndex.value < 0 || !conversation.value) return
  try {
    const res = await http.put<{ ok: boolean; conversation: ConversationDetail }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${conversation.value.task_id}/messages/${editingIndex.value}`,
      { content: editContent.value },
      { params: { account_id: botId.value } },
    )
    conversation.value = res.data?.conversation || conversation.value
    editDialog.value = false
    notify.push('消息已更新', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function addMessage() {
  if (!conversation.value || !newMessageContent.value.trim()) return
  try {
    const res = await http.post<{ ok: boolean; conversation: ConversationDetail }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${conversation.value.task_id}/messages`,
      { role: newMessageRole.value, content: newMessageContent.value.trim() },
      { params: { account_id: botId.value } },
    )
    conversation.value = res.data?.conversation || conversation.value
    newMessageContent.value = ''
    newMessageRole.value = 'user'
    await loadConversations()
    await loadSessions()
    notify.push('消息已添加', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function deleteMessage(index: number) {
  if (!conversation.value || !window.confirm(`确认删除第 ${index + 1} 条消息？`)) return
  try {
    const res = await http.delete<{ ok: boolean; conversation: ConversationDetail }>(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${conversation.value.task_id}/messages/${index}`,
      { params: { account_id: botId.value } },
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
      { params: { account_id: botId.value, format }, responseType: 'blob' },
    )
    const blob = res.data instanceof Blob ? res.data : new Blob([res.data])
    const baseName = safeFileName(conversation.value.title || '对话')
    const filename = format === 'json' ? `${baseName}.json` : `${baseName}.txt`
    saveBlob(blob, filename)
    notify.push('导出已开始', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

/** 列表行内导出：不必先点开对话（完整消息由服务端读取） */
async function exportConversationRow(target: ConversationSummary, format: 'text' | 'json') {
  if (botId.value == null || !selectedSessionId.value) return
  try {
    const res = await http.get(
      `/api/sessions/${encodeURIComponent(selectedSessionId.value)}/conversations/${target.task_id}/export`,
      { params: { account_id: botId.value, format }, responseType: 'blob' },
    )
    const blob = res.data instanceof Blob ? res.data : new Blob([res.data])
    const baseName = safeFileName(target.title || '对话')
    saveBlob(blob, format === 'json' ? `${baseName}.json` : `${baseName}.txt`)
    notify.push('导出已开始', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

/** 消息区回到最新一条（对话很长时不用手动滚到底） */
function scrollToLatest() {
  const el = messageBodyEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

// 打开对话后默认停在最新消息
watch(
  () => conversation.value?.task_id,
  async () => {
    await nextTick()
    scrollToLatest()
  },
)

watch(botId, (_next, previous) => {
  // 切账号：作废所有在途请求，避免上一个账号的会话/对话盖到新账号上
  sessionsSeq += 1
  listSeq += 1
  conversationSeq += 1
  loadingSessions.value = false
  loadingConversations.value = false
  loadingConversation.value = false
  sessionsError.value = ''
  conversationsError.value = ''
  selectedSessionId.value = ''
  selectedTaskId.value = ''
  selectedTaskIds.value = []
  conversation.value = null
  sessionQuery.value = ''
  conversationQuery.value = ''
  // 首次从「还没选中账号」变为有账号（深链直接打开带 ?session=）时保留 query，
  // 否则会把外部链接指定的会话清掉；真正的账号切换（previous 非空）才清。
  if (previous != null) syncSessionQuery('')
  loadSessions()
})

// 地址栏里的 ?session= 变化（例如从数据统计页点「查看会话」回来）时同步选中
watch(
  () => route.query.session,
  () => {
    applyQuerySelection()
  },
)

onMounted(() => {
  loadSessions()
})
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div>
        <h1 class="app-page-title">会话数据</h1>
        <div class="app-page-subtitle">会话是群聊 / 私聊记录，对话是一次任务保存的对话线程</div>
      </div>
      <div class="page-header-actions">
        <v-btn variant="text" prepend-icon="mdi-chart-bar" @click="router.push('/stats')">用量统计</v-btn>
        <v-chip v-if="mediaStats" size="small" variant="tonal" :loading="loadingMedia">
          <v-icon icon="mdi-image-multiple" start size="small" />
          {{ mediaStats.count }} 张 · {{ fmtBytes(mediaStats.bytes) }} · 可清理 {{ mediaStats.unreferenced }}
        </v-chip>
        <v-btn
          v-if="mediaStats && mediaStats.unreferenced > 0"
          variant="tonal"
          prepend-icon="mdi-broom"
          :loading="purgingMedia"
          @click="purgeMedia"
        >
          清理未引用图片
        </v-btn>
        <v-btn v-if="selectedSessionId" variant="tonal" prepend-icon="mdi-file-upload-outline" @click="exportSession">
          导出当前会话
        </v-btn>
        <v-btn variant="tonal" prepend-icon="mdi-upload" @click="openRestorePicker">导入备份</v-btn>
        <v-btn v-if="selectedSessionId" variant="tonal" prepend-icon="mdi-delete-empty" color="error" @click="deleteSession">
          删除当前会话
        </v-btn>
        <v-btn variant="tonal" prepend-icon="mdi-refresh" @click="loadSessions">刷新</v-btn>
      </div>
      <input
        ref="restoreFileInput"
        type="file"
        accept="application/zip,application/json,.zip,.json"
        class="d-none"
        @change="onRestoreFile"
      />
      <input
        ref="mediaFileInput"
        type="file"
        accept="image/*"
        class="d-none"
        @change="onReplacementPicked"
      />
    </div>

    <v-row class="mt-2">
      <v-col cols="12" md="3">
        <v-card variant="outlined" class="h-full">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-chat-outline" class="mr-2" color="primary" /> 会话列表
          </v-card-title>
          <v-card-text class="pa-2">
            <v-text-field
              v-model="sessionQuery"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              prepend-inner-icon="mdi-magnify"
              placeholder="搜索会话"
              class="list-search mb-2"
            />
            <v-progress-linear v-if="loadingSessions" indeterminate color="primary" />
            <template v-else-if="botId == null">
              <div class="text-caption text-center pa-4">请先选择一个账号</div>
            </template>
            <v-alert v-else-if="sessionsError" type="error" variant="tonal" density="compact" class="ma-2">
              <div class="text-caption mb-1">会话列表加载失败：{{ sessionsError }}</div>
              <v-btn size="x-small" variant="text" prepend-icon="mdi-refresh" @click="loadSessions">重试</v-btn>
            </v-alert>
            <template v-else-if="sessions.length === 0">
              <div class="empty-block">
                <div class="text-caption">账号连上协议端并收到消息后，这里会自动出现记录。</div>
                <div class="d-flex justify-center flex-wrap gap-2 mt-2">
                  <v-btn size="small" variant="tonal" prepend-icon="mdi-robot-outline" @click="router.push('/bots')">
                    去账号管理
                  </v-btn>
                  <v-btn size="small" variant="text" prepend-icon="mdi-console" @click="router.push('/logs')">
                    看连接日志
                  </v-btn>
                </div>
              </div>
            </template>
            <template v-else-if="filteredSessions.length === 0">
              <div class="text-caption text-center pa-4">没有匹配的会话</div>
            </template>
            <v-list v-else density="compact">
              <v-list-item
                v-for="s in filteredSessions"
                :key="s.session_id"
                :active="selectedSessionId === s.session_id"
                @click="selectSession(s.session_id)"
              >
                <template #prepend>
                  <v-icon :icon="s.type === 'group' ? 'mdi-account-group' : 'mdi-account'" color="primary" />
                </template>
                <v-list-item-title class="text-body-2">{{ sessionTitle(s) }}</v-list-item-title>
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
            <v-spacer />
            <v-btn
              v-if="selectedTaskIds.length"
              size="x-small"
              variant="tonal"
              color="error"
              prepend-icon="mdi-delete"
              :disabled="bulkDeleting"
              :loading="bulkDeleting"
              @click="bulkDeleteConversations"
            >
              删除选中
            </v-btn>
          </v-card-title>
          <v-card-text class="pa-2">
            <v-text-field
              v-model="conversationQuery"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              prepend-inner-icon="mdi-magnify"
              placeholder="搜索对话"
              class="list-search mb-2"
            />
            <v-progress-linear v-if="loadingConversations" indeterminate color="primary" />
            <template v-else-if="!selectedSessionId">
              <div class="text-caption text-center pa-4">先从会话列表选一个</div>
            </template>
            <v-alert v-else-if="conversationsError" type="error" variant="tonal" density="compact" class="ma-2">
              <div class="text-caption mb-1">对话列表加载失败：{{ conversationsError }}</div>
              <v-btn size="x-small" variant="text" prepend-icon="mdi-refresh" @click="loadConversations(false)">重试</v-btn>
            </v-alert>
            <template v-else-if="conversations.length === 0">
              <div class="text-caption text-center pa-4">这个会话还没有对话</div>
            </template>
            <template v-else-if="filteredConversations.length === 0">
              <div class="text-caption text-center pa-4">没有匹配的对话</div>
            </template>
            <v-list v-else density="compact">
              <v-list-item
                v-for="c in filteredConversations"
                :key="c.task_id"
                :active="selectedTaskId === c.task_id"
                @click="openConversation(c.task_id)"
              >
                <template #prepend>
                  <v-checkbox-btn
                    :model-value="selectedTaskIds.includes(c.task_id)"
                    density="compact"
                    @update:model-value="toggleTaskSelection(c.task_id)"
                    @click.stop
                  />
                </template>
                <v-list-item-title class="text-body-2">{{ c.title }}</v-list-item-title>
                <v-list-item-subtitle class="text-caption">
                  {{ c.messages }} 条消息 · {{ fmtTime(c.saved_at) }}
                </v-list-item-subtitle>
                <template #append>
                  <v-menu location="bottom end">
                    <template #activator="{ props }">
                      <v-btn
                        v-bind="props"
                        size="x-small"
                        variant="text"
                        icon="mdi-download"
                        title="导出这个对话"
                        @click.stop
                      />
                    </template>
                    <v-list density="compact">
                      <v-list-item prepend-icon="mdi-file-document-outline" @click="exportConversationRow(c, 'text')">
                        导出 TXT
                      </v-list-item>
                      <v-list-item prepend-icon="mdi-json" @click="exportConversationRow(c, 'json')">
                        导出 JSON
                      </v-list-item>
                    </v-list>
                  </v-menu>
                  <v-btn size="x-small" variant="text" icon="mdi-pencil" title="重命名" :disabled="deletingTaskId !== ''" @click.stop="renameConversation(c)" />
                  <v-btn
                    size="x-small"
                    variant="text"
                    icon="mdi-delete"
                    color="error"
                    title="删除对话"
                    :disabled="deletingTaskId !== ''"
                    :loading="deletingTaskId === c.task_id"
                    @click.stop="deleteConversation(c)"
                  />
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
            <v-spacer />
            <v-btn size="small" variant="tonal" prepend-icon="mdi-download" @click="exportConversation('text')">导出 TXT</v-btn>
            <v-btn size="small" variant="tonal" prepend-icon="mdi-json" @click="exportConversation('json')">导出 JSON</v-btn>
          </v-card-title>
          <v-card-title v-else class="text-caption">从对话列表选一个，消息显示在这里</v-card-title>

          <v-card-text class="pa-2">
            <v-progress-linear v-if="loadingConversation" indeterminate color="primary" />
            <template v-else-if="!conversation">
              <div class="text-caption text-center pa-6">这个对话还没有消息</div>
            </template>
            <div v-else ref="messageBodyEl" class="session-messages" style="max-height: calc(100vh - 320px); overflow-y: auto">
              <div v-for="(msg, index) in conversation.messages" :key="index" class="message-row">
                <div class="message-meta">
                  <v-chip size="x-small" :color="roleColor(msg.role)" variant="tonal">{{ roleLabel(msg.role) }}</v-chip>
                  <span v-if="msg.role === 'user'" class="text-caption">
                    {{ msg.nickname ? `${msg.nickname}(${msg.user_id || '?'})` : `QQ ${msg.user_id || '?'}` }}
                  </span>
                  <span v-if="msg.message_id" class="text-caption" style="opacity: 0.55">消息编号 {{ msg.message_id }}</span>
                  <span class="text-caption" style="opacity: 0.55">{{ fmtTime(msg.time) }}</span>
                  <v-spacer />
                  <v-btn size="x-small" variant="text" icon="mdi-pencil" title="编辑消息" @click="openEditMessage(index)" />
                  <v-btn size="x-small" variant="text" icon="mdi-delete" color="error" title="删除消息" @click="deleteMessage(index)" />
                </div>
                <div v-if="msg.images && msg.images.length" class="message-images">
                  <div v-for="image in msg.images" :key="`${image.seg}-${image.sha}`" class="image-tile">
                    <button
                      v-if="image.status === 'cached'"
                      type="button"
                      class="image-thumb"
                      :title="`点击预览 · ${fmtBytes(image.bytes)}`"
                      @click="openImagePreview(image)"
                    >
                      <img :src="imageSrc(image)" alt="消息里的图片" loading="lazy" />
                    </button>
                    <div v-else class="image-missing" :title="image.status === 'missing' ? '本地文件已丢失（可从备份恢复）' : '这张图没有落盘'">
                      <v-icon icon="mdi-image-off" size="20" />
                      <span class="text-caption">{{ image.status === 'missing' ? '文件缺失' : '未下载' }}</span>
                    </div>
                    <div class="image-actions">
                      <v-btn
                        size="x-small"
                        variant="text"
                        icon="mdi-swap-horizontal"
                        title="用新图片替换"
                        :loading="imageBusy === `replace-${index}-${image.seg}`"
                        @click="pickReplacement(index, image)"
                      />
                      <v-btn
                        size="x-small"
                        variant="text"
                        icon="mdi-image-remove"
                        color="error"
                        title="从消息里移除这张图"
                        :loading="imageBusy === `remove-${index}-${image.seg}`"
                        @click="removeImage(index, image)"
                      />
                    </div>
                  </div>
                </div>
                <div class="message-content">{{ msg.content }}</div>
              </div>
              <div v-if="!conversation.messages.length" class="text-caption text-center pa-6">该对话没有消息</div>
            </div>

            <div v-if="conversation?.messages.length" class="jump-latest-wrap">
              <v-btn size="x-small" variant="tonal" prepend-icon="mdi-arrow-down" @click="scrollToLatest">
                跳到最新
              </v-btn>
            </div>

            <v-divider v-if="conversation" class="my-3" />

            <v-form v-if="conversation" class="add-message-form" @submit.prevent="addMessage">
              <div class="d-flex align-center gap-2">
                <v-select
                  v-model="newMessageRole"
                  :items="[
                    { title: '用户', value: 'user' },
                    { title: '助手', value: 'assistant' },
                    { title: '系统', value: 'system' },
                  ]"
                  density="compact"
                  hide-details
                  class="add-role"
                />
                <v-text-field
                  v-model="newMessageContent"
                  placeholder="新增消息内容"
                  density="compact"
                  hide-details
                  class="add-content"
                />
                <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" type="submit" :disabled="!newMessageContent.trim()">
                  添加
                </v-btn>
              </div>
            </v-form>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-dialog v-model="editDialog" max-width="560">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-pencil" class="mr-2" color="primary" />
          编辑消息
        </v-card-title>
        <v-card-text>
          <v-textarea v-model="editContent" auto-grow rows="5" label="消息内容" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="editDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" @click="saveEditMessage">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="!!previewImage" max-width="860" @update:model-value="previewImage = null">
      <v-card v-if="previewImage">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-image" class="mr-2" color="primary" />
          本地图片
          <v-chip size="x-small" variant="tonal" class="ml-2">格式 {{ previewImage.mime.split('/')[1]?.toUpperCase() || '图片' }}</v-chip>
          <v-chip size="x-small" variant="tonal" class="ml-1">{{ fmtBytes(previewImage.bytes) }}</v-chip>
          <v-spacer />
          <span class="text-caption" style="opacity: 0.55">校验值 {{ previewImage.sha.slice(0, 8) }}</span>
        </v-card-title>
        <v-card-text class="text-center">
          <img :src="imageSrc(previewImage)" alt="本地图片" style="max-width: 100%; max-height: 70vh" />
        </v-card-text>
        <v-card-actions>
          <span class="text-caption ml-2" style="opacity: 0.65">
            预览的就是实际发送给模型的那张图。
          </span>
          <v-spacer />
          <v-btn variant="text" @click="previewImage = null">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.h-full {
  height: 100%;
}

.page-header-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.list-search {
  flex: 0 0 auto;
}

.empty-block {
  padding: 20px 16px;
  text-align: center;
  line-height: 1.7;
}

.jump-latest-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}

@media (max-width: 720px) {
  .page-header-actions {
    justify-content: flex-start;
    width: 100%;
  }
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

.message-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 6px 0 8px;
}

.image-tile {
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  background: rgba(var(--v-theme-on-surface), 0.03);
}

.image-thumb {
  display: block;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: zoom-in;
}

.image-thumb img {
  display: block;
  width: 132px;
  height: 132px;
  object-fit: cover;
}

.image-missing {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 132px;
  height: 132px;
  opacity: 0.6;
}

.image-actions {
  position: absolute;
  top: 2px;
  right: 2px;
  display: flex;
  gap: 2px;
  background: rgba(var(--v-theme-surface), 0.82);
  border-radius: 6px;
}

.add-message-form {
  padding: 0 4px;
}

.add-role {
  width: 120px;
  flex: 0 0 auto;
}

.add-content {
  flex: 1 1 auto;
}
</style>
