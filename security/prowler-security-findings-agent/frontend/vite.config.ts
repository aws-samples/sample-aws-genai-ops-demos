import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { port: 3000 },
  define: {
    // @smithy/signature-v4 references the Node.js Buffer global during
    // HMAC key derivation. Provide a minimal shim so the browser build works.
    global: 'globalThis',
  },
});
