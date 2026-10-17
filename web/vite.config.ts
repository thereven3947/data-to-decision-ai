/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server proxies /api to the d2d API (`uv run d2d serve`, default port 8000).
const api = process.env.D2D_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, proxy: { '/api': api } },
  test: { environment: 'node' },
})
