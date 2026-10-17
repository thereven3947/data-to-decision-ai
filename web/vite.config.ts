/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server proxies /api to the d2d API (`uv run d2d serve`, default port 8000).
const api = process.env.D2D_API_URL ?? 'http://127.0.0.1:8000'

// GitHub Pages demo build: VITE_DEMO=1 VITE_BASE=/data-to-decision-ai/ npm run build
const demoCsp = {
  name: 'demo-csp',
  transformIndexHtml(html: string) {
    if (process.env.VITE_DEMO !== '1') return html
    const csp =
      "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'"
    return html.replace('<head>', `<head>\n    <meta http-equiv="Content-Security-Policy" content="${csp}" />`)
  },
}

export default defineConfig({
  base: process.env.VITE_BASE ?? '/',
  plugins: [react(), demoCsp],
  server: { host: '127.0.0.1', port: 5173, proxy: { '/api': api } },
  test: { environment: 'node' },
})
