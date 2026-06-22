import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
  test: {
    include: [
      'test/**/*.test.ts',
    ],
    globals: true,
    testTimeout: 30000,
  },
  resolve: {
    alias: {
      '@src': path.resolve(__dirname, 'src'),
      '@test': path.resolve(__dirname, 'test'),
    },
  },
});
