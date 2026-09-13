import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/login': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/register': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/logout': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/settings': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/status': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/start': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/stop': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/preview': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/recipients': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/check-replies': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/track': { target: 'http://127.0.0.1:5001', changeOrigin: true },
      '/tracking': { target: 'http://127.0.0.1:5001', changeOrigin: true },
    }
  }
})
