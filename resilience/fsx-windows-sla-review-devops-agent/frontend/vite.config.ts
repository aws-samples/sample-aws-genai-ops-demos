import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The site and its API share one origin (CloudFront routes /admin/* to the Lab API), so
// nothing is configured at build time. For local work, point the proxy at a deployed Lab URL:
//   LAB_URL=https://dxxxx.cloudfront.net npm run dev   (Basic Auth is asked by the browser)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: process.env.LAB_URL ? { '/admin': { target: process.env.LAB_URL, changeOrigin: true } } : undefined,
  },
})
