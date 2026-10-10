import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Mirror the production nginx route so `npm run dev` also talks to /api.
  server: {
    proxy: {
      // 8060 is where the development stack publishes the backend (BACKEND_PORT in .env).
      '/api': { target: process.env.BACKEND_URL || 'http://localhost:8060', rewrite: (path) => path.replace(/^\/api/, '') },
    },
  },
})
