import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
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
