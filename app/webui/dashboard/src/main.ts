// 构建产物哈希失配（重建后旧 chunk 被换掉）时，动态 import 会失败。
// 这里自动刷新一次拿新的入口清单；用 sessionStorage 防刷新死循环。
const RELOAD_KEY = 'qqbot:chunk-reload-at'
window.addEventListener('vite:preloadError', (event) => {
  const last = Number(sessionStorage.getItem(RELOAD_KEY) || 0)
  if (Date.now() - last < 10_000) {
    console.error('[chunk] 短时间内重复失败，停止自动刷新', event)
    return
  }
  sessionStorage.setItem(RELOAD_KEY, String(Date.now()))
  event.preventDefault()
  window.location.reload()
})

import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import pinia from './stores'
import vuetify from './plugins/vuetify'
import { useNotifyStore } from './stores/notify'

// 本地字体（Outfit，拉丁字形；中文回退到系统字体栈）
import '@fontsource/outfit/400.css'
import '@fontsource/outfit/500.css'
import '@fontsource/outfit/600.css'
import '@fontsource/outfit/700.css'
// 全局样式：卡片规范 / 工具类 / 滚动条
import './styles/main.css'

const app = createApp(App)
app.use(pinia).use(router).use(vuetify)

/**
 * 全局错误兜底：Vue 组件/生命周期/事件处理器里的异常，以及没人 catch 的 Promise rejection。
 *
 * 只做「控制台保留完整堆栈 + 用户收到一次提示」，避免操作静默失败；
 * 渲染期异常导致的局部白屏由 App.vue 里的 ErrorBoundary 兜住（可重试本页）。
 * 3s 内重复异常只提示一次，防止同一个故障刷屏。
 */
let lastReportedAt = 0
function reportGlobalError(source: string, err: unknown): void {
  console.error(`[global] ${source}`, err)
  const now = Date.now()
  if (now - lastReportedAt < 3000) return
  lastReportedAt = now
  try {
    useNotifyStore(pinia).push('页面出现异常，刚才的操作可能没有完成', 'error')
  } catch {
    /* Pinia 未就绪或通知 store 自身异常：保留控制台日志即可 */
  }
}

app.config.errorHandler = (err, _instance, info) => {
  reportGlobalError(`vue (${info})`, err)
}

window.addEventListener('unhandledrejection', (event) => {
  reportGlobalError('unhandledrejection', event.reason)
})

app.mount('#app')
