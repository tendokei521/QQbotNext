import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import http, { unwrap } from '@/api/http'
import { onSocketMessage } from '@/api/socket'

export type BotStatus = 'connected' | 'disconnected' | 'connecting' | 'reconnecting' | 'error'

export interface BotData {
  index: number
  status: BotStatus
  bot_id?: number | null
  ws_url?: string
  access_token?: string
  owner_id?: number | null
  auto_connect?: boolean
  login_info?: { user_id?: number; nickname?: string } | null
  last_error?: string | null
  [key: string]: any
}

export interface BotConfig {
  index: number
  ws_url: string
  access_token: string
  owner_id: number | null
  auto_connect: boolean
}

const CURRENT_BOT_KEY = 'qqbot_current_bot_index'

/** 账号（Bot）状态中心：列表 / 当前选中 / 连接控制 / WS 实时状态同步 */
export const useBotsStore = defineStore('bots', () => {
  const bots = ref<BotData[]>([])
  const loading = ref(false)
  const currentIndex = ref<number | null>(null)

  const currentBot = computed(() => bots.value.find((b) => b.index === currentIndex.value) ?? null)
  const connectedCount = computed(() => bots.value.filter((b) => b.status === 'connected').length)
  const botCount = computed(() => bots.value.length)

  function restoreSelection() {
    const stored = parseInt(localStorage.getItem(CURRENT_BOT_KEY) ?? '', 10)
    if (!Number.isNaN(stored) && bots.value.some((b) => b.index === stored)) {
      currentIndex.value = stored
    } else if (bots.value.length) {
      currentIndex.value = bots.value[0].index
    } else {
      currentIndex.value = null
    }
  }

  function selectBot(index: number) {
    currentIndex.value = index
    localStorage.setItem(CURRENT_BOT_KEY, String(index))
  }

  async function fetchBots(): Promise<BotData[]> {
    loading.value = true
    try {
      const data = await http.get<{ bots: BotData[] }>('/api/bots')
      bots.value = data.data.bots || []
      if (currentIndex.value === null || !bots.value.some((b) => b.index === currentIndex.value)) {
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

  async function addBotConfig(cfg: Partial<BotConfig>) {
    const data = await http.post('/api/bots/config/add', cfg)
    return unwrap(data.data)
  }

  async function deleteBotConfig(index: number) {
    const data = await http.post(`/api/bots/config/delete/${index}`)
    return unwrap(data.data)
  }

  async function connect(index: number) {
    const data = await http.post(`/api/bots/${index}/connect`)
    return unwrap(data.data)
  }

  async function disconnect(index: number) {
    const data = await http.post(`/api/bots/${index}/disconnect`)
    return unwrap(data.data)
  }

  async function reconnect(index: number) {
    const data = await http.post(`/api/bots/${index}/reconnect`)
    return unwrap(data.data)
  }

  function patchStatus(bot: Partial<BotData>) {
    const idx = bot.index
    const target = bots.value.find((b) => b.index === idx)
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

  return {
    bots,
    loading,
    currentIndex,
    currentBot,
    connectedCount,
    botCount,
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
