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
    // 每次构建清空 dist。
    //
    // 历史：这里曾是 `emptyOutDir: false`，理由是"旧 chunk 留着，正在使用的页面
    // 不会因为哈希失配而 import() 404"。代价是构建产物无限累积（实测 1077 个文件 /
    // 15 MB，其中绝大多数是无引用的历史 chunk），只能靠手动跑 `scripts/prune_dist.py`；
    // 而那个脚本按"最近 7 天"删文件，会误删懒加载 chunk（它们不在 index.html 里）。
    //
    // 现在选择"干净构建"：仓库里的 dist 就是一次完整构建的结果，可复现、可审查。
    // 升级到新版本时，已经打开的旧页面需要刷新——这是可接受的（后端 index.html 每次
    // 都返回 no-cache，刷新即拿到新 chunk）。
    emptyOutDir: true,
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
