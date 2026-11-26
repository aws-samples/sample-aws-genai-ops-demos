import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: { '/api': 'http://localhost:3001' },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
