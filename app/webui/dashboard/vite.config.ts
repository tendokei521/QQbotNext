import { fileURLToPath, URL } from 'node:url'
import { execSync } from 'node:child_process'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

/**
 * 构建戳：`<短 commit> <构建日期>`，显示在侧边栏底部。
 *
 * 起因：多次出现"改了没生效"——其实是浏览器缓存旧 JS / 跑的是另一个 clone /
 * 后端没重启。界面上有 commit 号，就能直接和 `git log` 对上，不用猜。
 * 拿不到 git（如从压缩包构建）时降级为 `dev`。
 */
function buildStamp(): string {
  const now = new Date()
  const date = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`
  const cwd = fileURLToPath(new URL('.', import.meta.url))
  try {
    const sha = execSync('git rev-parse --short HEAD', { cwd, encoding: 'utf8' }).trim()
    // dirty 只统计**源码**改动：dist 是构建产物，每次构建都会变，不该让它污染戳
    const dirty = execSync(
      'git status --porcelain --untracked-files=no -- . ":(exclude)dist"',
      { cwd, encoding: 'utf8' },
    ).trim()
    return `${sha}${dirty ? '+dirty' : ''} · ${date}`
  } catch {
    return `dev · ${date}`
  }
}

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  define: {
    __BUILD_STAMP__: JSON.stringify(buildStamp()),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // 构建产物由 FastAPI 静态挂载，使用相对路径保证任意前缀可部署
  base: './',
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    chunkSizeWarningLimit: 1500,
    // 不在每次构建时清空 dist：旧 chunk 留着，正在使用的页面不会因为
    // 哈希失配而 import() 404。累积的旧文件用 scripts/prune_dist.py 清理。
    emptyOutDir: false,
  },
  server: {
    port: 3000,
    // 开发时代理到 FastAPI 后端，避免跨域
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:9200',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:9200',
        ws: true,
        changeOrigin: true,
      },
      '/static': {
        target: 'http://127.0.0.1:9200',
        changeOrigin: true,
      },
    },
  },
})
