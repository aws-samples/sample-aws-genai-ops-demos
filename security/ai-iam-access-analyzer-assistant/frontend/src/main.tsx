import React from "react";
import ReactDOM from "react-dom/client";
import "@cloudscape-design/global-styles/index.css";
// Bridges the dark-mode gap between @cloudscape-design/chat-components
// and @cloudscape-design/components (mismatched CSS-variable hash
// suffixes). Imported AFTER global-styles so its overrides win. See the
// header comment in the file for context.
import "./styles/chat-components-dark-mode.css";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
