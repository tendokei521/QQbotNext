import axios, { AxiosError } from 'axios'

/** 服务端注入的 token 优先，其次 localStorage（开发模式可手动填写） */
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
