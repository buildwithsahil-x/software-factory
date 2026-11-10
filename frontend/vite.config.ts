import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The factory API runs on :8000; the dev server proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: process.env.FACTORY_API_URL ?? 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
