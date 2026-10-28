import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The console talks to the FastAPI app through this proxy, so there is no CORS setup.
const apiTarget = process.env.API_URL || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    // strictPort: if the port is taken, fail with a clear error instead of quietly
    // moving to another port that nothing points at.
    port: Number(process.env.UI_PORT) || 5173,
    strictPort: true,
    proxy: { '/api': { target: apiTarget, changeOrigin: true } },
  },
})
