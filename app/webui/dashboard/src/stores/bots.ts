import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import http, { errorMessage, unwrap } from '@/api/http'
import { onSocketMessage } from '@/api/socket'
import { useNotifyStore } from './notify'

export type BotStatus = 'connected' | 'disconnected' | 'connecting' | 'reconnecting' | 'error'

/** 连接方向：forward = 正向 WS（框架连出）/ reverse = 反向 WS（框架监听，OneBot 实现连入） */
export type BotConnMode = 'forward' | 'reverse'

/** 账号身份信息：登录返回的 login_info 与「上次登录账号」快照同构 */
export interface BotAccount {
  user_id?: number | string | null
  nickname?: string | null
  last_login_at?: number | null
  [key: string]: any
}

/**
 * 账号数据。
 *
 * - `id`：账号唯一凭据（用户在「添加账号」时设定、创建后不可变）。**只用于前后端判断**，
 *   不直接展示给用户；
 * - `name`：展示名，卡片标题/顶栏选择器都用它；
 * - `bot_id`：当前登录的 QQ 号，纯运行时信息（登录后才有，断线时前端回退快照展示）。
 */
export interface BotData {
  id: string
  name: string
  status: BotStatus
  bot_id?: number | null
  ws_url?: string
  access_token?: string
  owner_id?: number | null
  auto_connect?: boolean
  conn_mode?: BotConnMode
  /** 反向模式：监听是否就绪（用在等对端接入的过渡态） */
  listening?: boolean
  login_info?: BotAccount | null
  /** 上一次成功登录的账号快照（后端落盘）：断开后用于回退展示 */
  last_account?: BotAccount | null
  /** 后端已合并好：连接中/已连接为实时 login_info，否则回退快照 */
  account?: BotAccount | null
  last_error?: string | null
  [key: string]: any
}

export interface BotConfig {
  id: string
  name: string
  ws_url: string
  access_token: string
  owner_id: number | null
  auto_connect: boolean
  conn_mode: BotConnMode
}

/** 新增账号的请求体：id/name 由用户填，地址留空则由后端按模式补默认值 */
export interface BotAddPayload {
  id?: string
  name?: string
  conn_mode: BotConnMode
  ws_url?: string
}

export interface BotAddResult {
  id: string
  name: string
  conn_mode: BotConnMode
  ws_url: string
}

const CURRENT_BOT_KEY = 'qqbot_current_account_id'
/** 旧版按数组下标记忆当前账号；升级后把它清掉，避免把下标当成 id 用 */
const LEGACY_INDEX_KEY = 'qqbot_current_bot_index'

/** 账号身份是否有效（user_id/bot_id 为 0 或空视为无账号） */
function hasAccount(acc?: BotAccount | null): boolean {
  if (!acc) return false
  const id = Number(acc.user_id)
  if (!Number.isNaN(id) && id > 0) return true
  return !!acc.nickname
}

/**
 * 统一取账号身份：连接中/已连接用实时 login_info，
 * 离线回退到持久化的 last_account（两者都是快照，故展示层统一用 hasAccount 判定）。
 */
export function resolveAccount(bot: BotData): BotAccount | null {
  const live = bot.status === 'connected' && hasAccount(bot.login_info) ? bot.login_info : null
  return live ?? bot.login_info ?? bot.account ?? bot.last_account ?? null
}

/** 当前登录的 QQ 号（运行时信息，只用于展示；离线回退上次登录快照） */
export function resolvedBotId(bot: BotData): number | null {
  if (bot.bot_id) return bot.bot_id
  const id = Number(resolveAccount(bot)?.user_id)
  return Number.isNaN(id) || id <= 0 ? null : id
}

/** 卡片/下拉里显示的名字（id 不给用户看） */
export function displayName(bot: BotData): string {
  return bot.name || bot.id
}

/** 账号（Bot）状态中心：列表 / 当前选中 / 连接控制 / WS 实时状态同步 */
export const useBotsStore = defineStore('bots', () => {
  const bots = ref<BotData[]>([])
  const loading = ref(false)
  /** 当前选中账号的 id（不是下标：id 不可变，删除别的账号不会让它指向别处） */
  const currentId = ref<string | null>(null)

  const currentBot = computed(() => bots.value.find((b) => b.id === currentId.value) ?? null)
  const connectedCount = computed(() => bots.value.filter((b) => b.status === 'connected').length)
  const botCount = computed(() => bots.value.length)

  /** 当前账号 id（组件里拼 API 参数用；没有选中账号时为 null） */
  const currentAccountId = computed<string | null>(() => currentId.value)

  function restoreSelection() {
    localStorage.removeItem(LEGACY_INDEX_KEY)
    const stored = localStorage.getItem(CURRENT_BOT_KEY) ?? ''
    if (stored && bots.value.some((b) => b.id === stored)) {
      currentId.value = stored
    } else if (bots.value.length) {
      currentId.value = bots.value[0].id
      localStorage.setItem(CURRENT_BOT_KEY, currentId.value)
    } else {
      currentId.value = null
    }
  }

  function selectBot(id: string) {
    // 只在状态列表已包含该 id 时才选中，避免指向尚未同步的连接条目
    if (!bots.value.some((b) => b.id === id)) return
    currentId.value = id
    localStorage.setItem(CURRENT_BOT_KEY, id)
  }

  async function fetchBots(): Promise<BotData[]> {
    loading.value = true
    try {
      const data = await http.get<{ bots: BotData[] }>('/api/bots')
      bots.value = data.data.bots || []
      if (currentId.value === null || !bots.value.some((b) => b.id === currentId.value)) {
        restoreSelection()
      }
      return bots.value
    } finally {
      loading.value = false
    }
  }

  async function fetchBotConfig(): Promise<BotConfig[]> {
    const data = await http.get<{ bots: BotConfig[] }>('/api/bots/config')
    return data.data.bots || []
  }

  async function saveBotConfig(configs: BotConfig[]) {
    const data = await http.post('/api/bots/config/save', { bots: configs })
    return unwrap(data.data)
  }

  async function addBotConfig(payload: BotAddPayload): Promise<BotAddResult> {
    const data = await http.post('/api/bots/config/add', payload)
    return unwrap(data.data) as BotAddResult
  }

  async function deleteBotConfig(id: string) {
    const data = await http.post(`/api/bots/config/delete/${encodeURIComponent(id)}`)
    return unwrap(data.data)
  }

  async function connect(id: string) {
    const data = await http.post(`/api/bots/${encodeURIComponent(id)}/connect`)
    return unwrap(data.data)
  }

  async function disconnect(id: string) {
    const data = await http.post(`/api/bots/${encodeURIComponent(id)}/disconnect`)
    return unwrap(data.data)
  }

  async function reconnect(id: string) {
    const data = await http.post(`/api/bots/${encodeURIComponent(id)}/reconnect`)
    return unwrap(data.data)
  }

  function patchStatus(bot: Partial<BotData> & { id?: string }) {
    const id = bot.id
    if (!id) return
    const target = bots.value.find((b) => b.id === id)
    if (target) {
      Object.assign(target, bot)
    } else {
      bots.value.push(bot as BotData)
    }
  }

  // WS 实时状态同步
  onSocketMessage('bot_status_updated', (msg) => {
    if (msg.bot) patchStatus(msg.bot)
  })

  // WS 重连/首次连接成功后重拉状态，弥补断线期间丢失的 status 更新
  onSocketMessage('socket_open', () => {
    // 回调里没人 await：失败必须自己收口，否则是 unhandled rejection（连接重建后后端抖动很常见）
    fetchBots().catch((err) => {
      useNotifyStore().push(`账号状态刷新失败：${errorMessage(err)}`, 'warning')
    })
  })

  return {
    bots,
    loading,
    currentId,
    currentAccountId,
    currentBot,
    connectedCount,
    botCount,
    resolveAccount,
    resolvedBotId,
    restoreSelection,
    selectBot,
    fetchBots,
    fetchBotConfig,
    saveBotConfig,
    addBotConfig,
    deleteBotConfig,
    connect,
    disconnect,
    reconnect,
    patchStatus,
  }
})
