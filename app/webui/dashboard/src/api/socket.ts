import { token } from './http'

type MessageHandler = (msg: any) => void

const handlers = new Map<string, Set<MessageHandler>>()
let ws: WebSocket | null = null
let retryTimer: number | null = null
let manualClosed = false
/** WS 是否处于 OPEN。供「晚挂载」的页面查询：连接早于页面挂载时，socket_open 不会重放。 */
let connected = false
let logArrayHandler: MessageHandler | null = null
let logMode: 'simple' | 'raw' = 'simple'

/** 向某类订阅者分发一条广播（单个处理器异常不影响其他处理器）。 */
function dispatch(type: string, payload: Record<string, unknown> = {}): void {
  const set = handlers.get(type)
  if (!set) return
  const msg = { type, ...payload }
  set.forEach((h) => {
    try {
      h(msg)
    } catch {
      /* 单个处理器异常不影响其他处理器 */
    }
  })
}

/** 连接已断开：复位状态并通知订阅者（如「实时」徽标改回「重连中」）。 */
function markDisconnected(): void {
  if (!connected) return
  connected = false
  dispatch('socket_close')
}

/**
 * 当前 WS 是否已连上（且确实处于 OPEN）。
 *
 * 用途：全局 WS 是单例，通常由布局在应用启动时建立；之后才挂载的页面（如数据统计页）
 * 只能收到**未来**的 socket_open，靠监听该事件判断「已连接」会永远停在未连接状态。
 * 同时校验 readyState：旧连接关闭中、新连接握手中时不能误报「已连接」。
 */
export function isSocketConnected(): boolean {
  return connected && ws !== null && ws.readyState === WebSocket.OPEN
}

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
    markDisconnected() // 旧连接被主动换掉：先复位状态，等新连接 open 再置为已连接
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
  // 走到这里说明没有可用连接：若旧连接还在关闭途中（CLOSING），先复位状态，
  // 否则旧连接的 onclose 会因身份守卫被忽略，connected 会一直停在 true。
  markDisconnected()

  const t = token.get()
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const params = new URLSearchParams()
  if (t) params.set('token', t)
  params.set('mode', logMode)
  const query = `?${params.toString()}`
  const socket = new WebSocket(`${proto}://${location.host}/ws/logs${query}`)
  ws = socket

  socket.onopen = () => {
    if (ws !== socket) return // 已被 setLogMode 换掉的旧连接：忽略它的迟到事件
    connected = true
    // 广播“已连接/重连成功”，各 store 借此重拉状态与模块数据，弥补断线期间的丢失更新
    dispatch('socket_open')
  }

  socket.onmessage = (ev) => {
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
      dispatch(payload.type, payload)
    }
  }

  socket.onclose = () => {
    if (ws === socket) {
      ws = null
      markDisconnected()
    }
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
  markDisconnected()
  ws?.close()
  ws = null
}
