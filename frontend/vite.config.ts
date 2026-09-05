import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Dev convenience: call relative /api/v1 URLs and let Vite forward them
    // to the FastAPI backend (uvicorn on :8000). Override with a real
    // VITE_API_URL for other deployments.
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_URL ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
