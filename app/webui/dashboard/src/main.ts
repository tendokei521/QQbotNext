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

// 本地字体（Outfit，拉丁字形；中文回退到系统字体栈）
import '@fontsource/outfit/400.css'
import '@fontsource/outfit/500.css'
import '@fontsource/outfit/600.css'
import '@fontsource/outfit/700.css'
// 全局样式：卡片规范 / 工具类 / 滚动条
import './styles/main.css'

createApp(App).use(pinia).use(router).use(vuetify).mount('#app')
