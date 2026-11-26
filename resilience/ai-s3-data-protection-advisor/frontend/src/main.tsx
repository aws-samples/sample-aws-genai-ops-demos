import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import '@cloudscape-design/global-styles/index.css';
import App from './App';
import { loadConfig } from './config';

const root = ReactDOM.createRoot(document.getElementById('root')!);

loadConfig().then(
  () =>
    root.render(
      <React.StrictMode>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </React.StrictMode>,
    ),
  () =>
    root.render(
      <Box padding="l">
        <Alert type="error" header="Configuration error">
          The dashboard configuration could not be loaded.
        </Alert>
      </Box>,
    ),
);
