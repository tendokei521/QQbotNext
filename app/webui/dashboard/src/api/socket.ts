import { redirectToLogin, token } from './http'

type MessageHandler = (msg: any) => void

/** 实时连接状态：connecting=正在握手 / open=已连上 / closed=已断开（含重连等待中） */
export type SocketStatus = 'connecting' | 'open' | 'closed'

const handlers = new Map<string, Set<MessageHandler>>()
let ws: WebSocket | null = null
let retryTimer: number | null = null
let manualClosed = false
/** WS 是否处于 OPEN。供「晚挂载」的页面查询：连接早于页面挂载时，socket_open 不会重放。 */
let connected = false
let logArrayHandler: MessageHandler | null = null
let logMode: 'simple' | 'raw' = 'simple'

/** 连续重连失败次数（连上后归零）：用于计算退避时长，也用于顶栏「第 N 次重连」提示 */
let retryAttempt = 0
let status: SocketStatus = 'closed'
const BASE_RETRY_MS = 1000
const MAX_RETRY_MS = 30000
/** 鉴权失败/被策略拒绝：再怎么重连也不会成功，直接回登录页（与 HTTP 401 兜底同路） */
const LOGIN_REDIRECT_CODES = new Set([4401, 1008])

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

/**
 * 广播连接状态（供顶栏显示「实时连接断开/重连中」）。
 *
 * 契约：`socket_status` 的 payload 固定为 `{ status, attempt }`，
 * `attempt` 是当前这次连接之前的连续失败次数（0 = 首次连接/刚连上）。
 */
function broadcastStatus(next: SocketStatus): void {
  status = next
  dispatch('socket_status', { status: next, attempt: retryAttempt })
}

/** 连接已断开：复位状态并通知订阅者（如「实时」徽标改回「重连中」）。 */
function markDisconnected(): void {
  if (!connected) return
  connected = false
  dispatch('socket_close')
}

/** 当前连接状态与连续失败次数（供晚挂载的组件初始化显示，不必等下一次广播）。 */
export function getSocketStatus(): { status: SocketStatus; attempt: number } {
  return { status, attempt: retryAttempt }
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
  retryAttempt = 0
  if (ws) {
    const old = ws
    ws = null
    old.onclose = null
    markDisconnected() // 旧连接被主动换掉：先复位状态，等新连接 open 再置为已连接
    old.close()
  }
  connectSocket()
}

/** 订阅指定 type 的广播消息（bot_status_updated / module_config_updated / socket_status ...） */
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
  broadcastStatus('connecting')

  socket.onopen = () => {
    if (ws !== socket) return // 已被 setLogMode 换掉的旧连接：忽略它的迟到事件
    connected = true
    retryAttempt = 0 // 连上即复位退避：下一次断线重新从 1s 开始
    if (retryTimer !== null) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    // 广播“已连接/重连成功”，各 store 借此重拉状态与模块数据，弥补断线期间的丢失更新
    broadcastStatus('open')
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

  socket.onclose = (ev) => {
    if (ws !== socket) return // 已被替换/主动关闭的旧连接：它的事件不再影响当前状态
    ws = null
    markDisconnected()

    if (LOGIN_REDIRECT_CODES.has(ev.code) && !location.pathname.startsWith('/login')) {
      // 鉴权/策略拒绝：重连没有意义，和 HTTP 401 走同一条「先落盘再跳登录」的路
      manualClosed = true
      broadcastStatus('closed')
      void redirectToLogin()
      return
    }
    if (manualClosed) {
      broadcastStatus('closed')
      return
    }

    // 指数退避：1s → 2s → 4s …… 上限 30s；连上后由 onopen 复位
    const delay = Math.min(BASE_RETRY_MS * 2 ** retryAttempt, MAX_RETRY_MS)
    retryAttempt += 1
    broadcastStatus('closed')
    if (retryTimer !== null) clearTimeout(retryTimer)
    retryTimer = window.setTimeout(() => {
      retryTimer = null
      connectSocket()
    }, delay)
  }
}

export function closeSocket(): void {
  manualClosed = true
  if (retryTimer) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
  retryAttempt = 0
  markDisconnected()
  broadcastStatus('closed')
  ws?.close()
  ws = null
}
