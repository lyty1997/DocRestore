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
      },
    },
  },
})
