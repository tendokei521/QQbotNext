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

/**
 * 构建戳（`vite.config.ts` 注入）：`<短 commit> <构建日期>`。
 *
 * 用途：**判断"我改的到底有没有生效"**。浏览器缓存、clone 不是同一个目录、
 * 忘了重启/重建，症状都一样（"改了没用"）——界面上直接看到 commit 号，
 * 一眼就能对上仓库里那一次提交。
 */
declare const __BUILD_STAMP__: string

/** 应用版本（`vite.config.ts` 从 package.json 注入）：界面上只展示这个，不展示 commit。 */
declare const __APP_VERSION__: string

