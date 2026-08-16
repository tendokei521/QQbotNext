import { token } from './http'

type MessageHandler = (msg: any) => void

const handlers = new Map<string, Set<MessageHandler>>()
let ws: WebSocket | null = null
let retryTimer: number | null = null
let manualClosed = false
let logArrayHandler: MessageHandler | null = null

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
  const query = t ? `?token=${encodeURIComponent(t)}` : ''
  ws = new WebSocket(`${proto}://${location.host}/ws/logs${query}`)

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
