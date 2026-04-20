import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

/**
 * Vite configuration for G.O.A.T. frontend.
 *
 * Environment variables prefixed with VITE_ are injected at build time
 * and accessed via import.meta.env in the application code:
 *   - VITE_AGENT_RUNTIME_ARN
 *   - VITE_REGION
 *   - VITE_USER_POOL_ID
 *   - VITE_USER_POOL_CLIENT_ID
 *   - VITE_IDENTITY_POOL_ID
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@shared': path.resolve(__dirname, '../infrastructure/cdk/lib/shared'),
    },
  },
});
