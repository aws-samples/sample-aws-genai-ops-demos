import { Buffer } from 'buffer';
// @smithy/signature-v4 uses Node.js Buffer for HMAC key derivation.
// Expose it globally so the browser build works.
(window as any).Buffer = Buffer;

import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import '@cloudscape-design/global-styles/index-base.css';
import App from './App';
import { applyOneTheme } from './one-theme';
import { applyBrandTheme } from './theme';
import './index.css';

applyOneTheme();
applyBrandTheme();

// A redeploy deletes the previous build's page chunks. Reload to pick up the new build, at most
// every 10 s so a broken deploy cannot loop.
window.addEventListener('vite:preloadError', () => {
  try {
    const last = Number(sessionStorage.getItem('soc-chunk-reload') || 0);
    if (Date.now() - last < 10000) return;
    sessionStorage.setItem('soc-chunk-reload', String(Date.now()));
  } catch { /* storage unavailable: reload anyway */ }
  window.location.reload();
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
