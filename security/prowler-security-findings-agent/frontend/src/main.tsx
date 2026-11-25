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

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
