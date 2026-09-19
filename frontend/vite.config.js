import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the app calls /api/*, which is proxied to the local FastAPI
// server started by `python tasks.py api` in backend/.
//
// For a deployed build, set VITE_API_BASE to the API Gateway URL, e.g.
//   VITE_API_BASE=https://xxxx.execute-api.ap-south-1.amazonaws.com/v1 npm run build
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
