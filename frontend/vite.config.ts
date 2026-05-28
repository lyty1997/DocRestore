import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const IGNORED_WATCH_GLOBS = [
  '**/.mypy_cache/**',
  '**/.pytest_cache/**',
  '**/__pycache__/**',
  '**/dist/**',
  '**/build/**',
  '**/data/**',
]

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    watch: {
      ignored: IGNORED_WATCH_GLOBS,
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
        // 大批量图片上传单批可能跑几十秒；http-proxy 默认 socket 超时
        // 在某些 Node/中间件版本下会兜底为 ~120s，触发后浏览器侧报
        // "Failed to fetch"。0 表示不主动断流，由前端 AbortController 兜底。
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
})
