import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'
  const proxy = { target, changeOrigin: true, rewrite: (path: string) => path.replace(/^\/api\/v1/, '') || '/' }
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api/v1': proxy,
        '/api': { target, changeOrigin: true },
        '/ws': { target, ws: true, rewrite: (path: string) => path.replace(/^\/api\/v1/, '') },
      },
    },
  }
})
