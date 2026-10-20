import { token } from './http'

type MessageHandler = (msg: any) => void

const handlers = new Map<string, Set<MessageHandler>>()
let ws: WebSocket | null = null
let retryTimer: number | null = null
let manualClosed = false
let logArrayHandler: MessageHandler | null = null
let logMode: 'simple' | 'raw' = 'simple'

/** 切换日志推送模式（simple=用户简洁日志 / raw=原始日志），并立即重连日志 WS。 */
export function setLogMode(mode: 'simple' | 'raw'): void {
  if (logMode === mode) return
  logMode = mode
  if (retryTimer) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
  if (ws) {
    const old = ws
    ws = null
    old.onclose = null
    old.close()
  }
  connectSocket()
}

/** 订阅指定 type 的广播消息（bot_status_updated / module_config_updated ...） */
export function onSocketMessage(type: string, handler: MessageHandler): () => void {
  let set = handlers.get(type)
  if (!set) {
    set = new Set()
    handlers.set(type, set)
  }
  set.add(handler)
  return () => set!.delete(handler)
}

/** 订阅日志快照（服务端每秒推送一次最近日志数组） */
export function onLogSnapshot(handler: MessageHandler): () => void {
  logArrayHandler = handler
  return () => {
    if (logArrayHandler === handler) logArrayHandler = null
  }
}

export function connectSocket(): void {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return
  manualClosed = false

  const t = token.get()
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const params = new URLSearchParams()
  if (t) params.set('token', t)
  params.set('mode', logMode)
  const query = `?${params.toString()}`
  ws = new WebSocket(`${proto}://${location.host}/ws/logs${query}`)

  ws.onopen = () => {
    // 广播“已连接/重连成功”，各 store 借此重拉状态与模块数据，弥补断线期间的丢失更新
    const set = handlers.get('socket_open')
    set?.forEach((h) => {
      try {
        h({ type: 'socket_open' })
      } catch {
        /* 单个处理器异常不影响其他处理 */
      }
    })
  }

  ws.onmessage = (ev) => {
    let payload: any
    try {
      payload = JSON.parse(ev.data)
    } catch {
      return
    }
    // 数组 = 日志快照；对象带 type = 广播事件
    if (Array.isArray(payload)) {
      logArrayHandler?.(payload)
      return
    }
    if (payload && typeof payload.type === 'string') {
      const set = handlers.get(payload.type)
      set?.forEach((h) => {
        try {
          h(payload)
        } catch {
          /* 单个处理器异常不影响其他处理器 */
        }
      })
    }
  }

  ws.onclose = () => {
    ws = null
    if (!manualClosed) {
      retryTimer = window.setTimeout(connectSocket, 3000)
    }
  }
}

export function closeSocket(): void {
  manualClosed = true
  if (retryTimer) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
  ws?.close()
  ws = null
}
