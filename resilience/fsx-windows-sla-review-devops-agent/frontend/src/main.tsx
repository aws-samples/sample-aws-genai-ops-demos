import React from 'react'
import ReactDOM from 'react-dom/client'
import LabPage from './lab/LabPage'

// The Lab is the whole site: this demo has no application, only the presenter's control room.
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LabPage />
  </React.StrictMode>,
)
