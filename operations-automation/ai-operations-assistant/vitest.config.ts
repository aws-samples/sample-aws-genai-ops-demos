import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
  test: {
    include: [
      'tests/properties/**/*.test.ts',
      'tests/unit/**/*.test.ts',
    ],
    globals: true,
    testTimeout: 30000,
  },
  resolve: {
    alias: {
      '@shared': path.resolve(__dirname, 'infrastructure/cdk/lib/shared'),
      '@agents': path.resolve(__dirname, 'agents'),
    },
  },
});
