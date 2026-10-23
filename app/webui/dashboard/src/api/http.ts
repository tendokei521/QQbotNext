import axios, { AxiosError } from 'axios'

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

/**
 * 401 统一兜底：会话过期或 cookie 被清后整页跳到独立登录页（带当前地址以便登录后返回）。
 * 用整页跳转而非路由跳转——登录页是后端直出的独立页面，不在 SPA 路由表里。
 */
http.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError) => {
    if (error.response?.status === 401 && !location.pathname.startsWith('/login')) {
      const next = encodeURIComponent(location.pathname + location.search + location.hash)
      location.href = `/login?next=${next}`
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
    return body?.message || err.message || '网络请求失败'
  }
  if (err instanceof Error) return err.message
  return String(err)
}

export default http
