import axios, { AxiosError } from 'axios'
import { useNotifyStore } from '@/stores/notify'

/**
 * 鉴权凭据解析。
 *
 * 生产模式下浏览器靠 **HttpOnly 会话 cookie** 鉴权（登录页签发），页面里拿不到令牌，
 * 所以这里通常返回空串、不带 Authorization 头——这是预期行为，不是配置漏了。
 *
 * 保留两条回退路径：
 * - `window.WEBUI_TOKEN`：插件自定义页仍由服务端注入，页面 JS 需要它拼 <img>/WS 链接；
 * - localStorage：`vite dev` 直连后端时（没有登录页签发 cookie）手工填令牌用。
 */
function resolveToken(): string {
  const injected = window.WEBUI_TOKEN
  if (injected) return injected
  return localStorage.getItem('qqbot_webui_token') || ''
}

export const token = {
  get: resolveToken,
  set(value: string) {
    localStorage.setItem('qqbot_webui_token', value)
  },
  clear() {
    localStorage.removeItem('qqbot_webui_token')
  },
}

export const http = axios.create({
  baseURL: '/',
  timeout: 15000,
})

http.interceptors.request.use((config) => {
  const t = resolveToken()
  if (t) config.headers.Authorization = `Bearer ${t}`
  return config
})

/** 跳登录前的草稿落盘回调：返回 Promise 的回调会被等待（有超时上限）。 */
type DraftFlush = () => Promise<unknown> | unknown
const draftFlushes = new Set<DraftFlush>()

/** 注册一个「跳登录前先落盘」回调，返回注销函数（页面卸载时调用）。 */
export function registerDraftFlush(fn: DraftFlush): () => void {
  draftFlushes.add(fn)
  return () => draftFlushes.delete(fn)
}

/** 会话过期的确认握手机制：同一次过期只处理一次，避免并发 401 触发多次跳转/提示。 */
let redirecting = false

/**
 * 会话过期统一兜底：先给未保存草稿一次落盘机会，再整页跳独立登录页（带当前地址以便登录后返回）。
 *
 * 用整页跳转而非路由跳转——登录页是后端直出的独立页面，不在 SPA 路由表里。
 * 落盘请求本身也可能 401，所以只等最多 800ms：不能让用户卡在已过期的页面上。
 */
export async function redirectToLogin(reason = '登录已过期'): Promise<void> {
  if (redirecting || location.pathname.startsWith('/login')) return
  redirecting = true
  const next = encodeURIComponent(location.pathname + location.search + location.hash)
  try {
    // 提示必须在整页导航前发出：导航会清掉当前文档里的通知
    useNotifyStore().push(`${reason}，正在返回登录页…`, 'warning')
  } catch {
    /* Pinia 尚未就绪（极早期请求）时忽略提示 */
  }
  window.dispatchEvent(new CustomEvent('qqbot:session-expired'))
  try {
    if (draftFlushes.size) {
      const pending = Promise.allSettled([...draftFlushes].map((fn) => Promise.resolve().then(fn)))
      await Promise.race([pending, new Promise((resolve) => setTimeout(resolve, 800))])
    }
  } finally {
    location.href = `/login?next=${next}`
  }
}

/**
 * 401 统一兜底：会话过期或 cookie 被清后整页跳到独立登录页（带当前地址以便登录后返回）。
 * 跳转前会给已注册的页面一次保存草稿的机会（见 registerDraftFlush）。
 */
http.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError) => {
    if (error.response?.status === 401 && !location.pathname.startsWith('/login')) {
      void redirectToLogin()
    }
    return Promise.reject(error)
  },
)

/** 响应外层为 {status: 'success'|'error', message?, ...} 时统一解包；
 *  业务数据通常在顶层（如 {bots: [...]}）或 data 字段，按端点各自处理。 */
export interface ApiEnvelope<T = any> {
  status?: string
  message?: string
  data?: T
  [key: string]: any
}

export function unwrap<T = any>(payload: ApiEnvelope<T>): T {
  if (payload && payload.status === 'error') {
    throw new Error(payload.message || '请求失败')
  }
  return (payload?.data ?? payload) as T
}

export function errorMessage(err: unknown): string {
  if (err instanceof AxiosError) {
    const body = err.response?.data as ApiEnvelope | undefined
    const message = body?.message || err.message || '网络请求失败'
    // 安装/预检类错误后端会带 hint（可操作建议）；有就拼在消息后面
    const hint = typeof body?.hint === 'string' ? body.hint.trim() : ''
    return hint && !message.includes(hint) ? `${message}（${hint}）` : message
  }
  if (err instanceof Error) return err.message
  return String(err)
}

export default http
