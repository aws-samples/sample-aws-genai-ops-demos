import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// The Lab UI is shared (../../../../shared/lab/frontend) and lives outside this package,
// so its bare imports must resolve to THIS project's copies. `dedupe` does that for the
// packages it imports; tsconfig.json `paths` does the same for type-checking.
const SHARED_LAB_DEPENDENCIES = ['react', 'react-dom', 'react-markdown', 'remark-gfm', '@cloudscape-design/components', '@cloudscape-design/global-styles']

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
    dedupe: SHARED_LAB_DEPENDENCIES,
  },
  server: {
    port: 5173,
    fs: { allow: [path.resolve(__dirname), path.resolve(__dirname, '../../../../shared/lab/frontend')] },
    proxy: {
      '/api': {
        target: 'http://localhost:3000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:3000',
        changeOrigin: true,
      },
      '/admin': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
    },
  },
})
