import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Dev proxy: /api + /ws -> FastAPI on 8123 (see deploy/README-pg.md workflow).
// Production: `npm run build` -> FastAPI serves web/dist (single process).
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8123', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8123', ws: true },
    },
  },
})
