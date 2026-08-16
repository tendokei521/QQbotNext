/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

// FastAPI 服务端注入的引导数据（生产模式下由 Jinja 模板写入）
interface Window {
  WEBUI_TOKEN?: string
  WEBUI_CONFIG?: Record<string, any>
  BOTS_DATA?: Record<string, any>[]
}
