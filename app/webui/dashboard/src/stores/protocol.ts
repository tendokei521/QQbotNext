import { defineStore } from 'pinia'
import { ref } from 'vue'
import http, { errorMessage } from '@/api/http'

export interface ProtocolItem {
  protocol: string
  display_name: string
  homepage: string
  versions: string[]
  notes?: Record<string, string>
  installed: boolean
  running: boolean
  version: string
  pid: number | null
  uptime_s: number
  restarts: number
  last_exit?: { code?: number; at?: number; reason?: string } | null
  log_path: string
}

export interface LoginState {
  online: boolean
  user_id: number | string
  nickname: string
}

/**
 * 协议端（NapCat 等）面板：安装固定版本、启停、看日志、扫码登录。
 *
 * 设计意图：用户不需要打开协议端自己的面板——这里能回答"它起没起、为什么退、
 * 日志最后几行、二维码在哪"。
 */
export const useProtocolStore = defineStore('protocol', () => {
  const items = ref<ProtocolItem[]>([])
  const loading = ref(false)
  const busy = ref('')
  const error = ref('')

  async function load(): Promise<void> {
    loading.value = true
    try {
      const res = await http.get('/api/protocol')
      items.value = res.data?.protocols || []
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function action(name: string, path: string, body: Record<string, any> = {}): Promise<any> {
    busy.value = `${name}:${path}`
    try {
      const res = await http.post(`/api/protocol/${encodeURIComponent(name)}/${path}`, body)
      await load()
      return res.data
    } finally {
      busy.value = ''
    }
  }

  const install = (name: string, version?: string) => action(name, 'install', version ? { version } : {})
  const start = (name: string) => action(name, 'start')
  const stop = (name: string) => action(name, 'stop')
  const restart = (name: string) => action(name, 'restart')

  async function log(name: string, lines = 100): Promise<string> {
    const res = await http.get(`/api/protocol/${encodeURIComponent(name)}/log`, { params: { lines } })
    return res.data?.log || ''
  }

  async function qrcode(name: string): Promise<{ image: string; content: string }> {
    const res = await http.get(`/api/protocol/${encodeURIComponent(name)}/qrcode`)
    return { image: res.data?.image || '', content: res.data?.content || '' }
  }

  async function loginStatus(name: string): Promise<LoginState> {
    const res = await http.get(`/api/protocol/${encodeURIComponent(name)}/login-status`)
    return res.data as LoginState
  }

  async function completeLogin(name: string, accountId: string): Promise<any> {
    const res = await http.post(`/api/protocol/${encodeURIComponent(name)}/login/complete`, {
      account_id: accountId,
    })
    return res.data
  }

  return {
    items,
    loading,
    busy,
    error,
    load,
    install,
    start,
    stop,
    restart,
    log,
    qrcode,
    loginStatus,
    completeLogin,
  }
})
