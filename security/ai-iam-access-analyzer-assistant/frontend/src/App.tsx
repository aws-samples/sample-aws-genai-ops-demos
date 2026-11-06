import { useState, useLayoutEffect, useCallback } from "react";
import { Amplify } from "aws-amplify";
import { Authenticator } from "@aws-amplify/ui-react";
import "@aws-amplify/ui-react/styles.css";
import { applyMode, Mode } from "@cloudscape-design/global-styles";
import AppLayout from "@cloudscape-design/components/app-layout";
import TopNavigation from "@cloudscape-design/components/top-navigation";
import ChatInterface from "./components/ChatInterface";
import WelcomeModal from "./components/WelcomeModal";

// Configure Amplify with environment variables (set at deploy time)
Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: import.meta.env.VITE_USER_POOL_ID,
      userPoolClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID,
    },
  },
});

const DARK_MODE_CLASS = "awsui-dark-mode";
const DARK_MODE_STORAGE_KEY = "iam-analyzer-dark-mode";

/**
 * Cloudscape's design tokens flip to dark mode when an ancestor of the
 * rendered tree carries the ``awsui-dark-mode`` class. `applyMode` from
 * `@cloudscape-design/global-styles` handles that, but under
 * `<Authenticator>` (Amplify UI) we observed the class going missing —
 * the outer TopNavigation darkened through other paths while the
 * AppLayout content stayed on the light tokens. Root cause was a mix of
 * a module-scope `applyMode` call running before `document.body` exists
 * and Authenticator's re-render timing. Apply the class to both
 * `<html>` and `<body>` ourselves, in addition to calling `applyMode`,
 * so no matter which element downstream code is watching, the tokens
 * flip together.
 */
function applyDarkModeClasses(isDark: boolean): void {
  if (typeof document === "undefined") {
    return;
  }
  const targets = [document.documentElement, document.body].filter(
    (el): el is HTMLElement => Boolean(el)
  );
  for (const el of targets) {
    if (isDark) {
      el.classList.add(DARK_MODE_CLASS);
    } else {
      el.classList.remove(DARK_MODE_CLASS);
    }
  }
  applyMode(isDark ? Mode.Dark : Mode.Light);
}

// Compute the initial mode + write the class BEFORE React renders so
// there's no light-mode flash on first paint. This runs synchronously as
// the module loads; `document.documentElement` exists at this point even
// if `document.body` doesn't yet.
const savedMode = localStorage.getItem(DARK_MODE_STORAGE_KEY);
const initialDark = savedMode !== null
  ? savedMode === "true"
  : (typeof window !== "undefined" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
applyDarkModeClasses(initialDark);

function App() {
  const [darkMode, setDarkMode] = useState(initialDark);

  const toggleDarkMode = useCallback(() => {
    setDarkMode((prev) => {
      const next = !prev;
      localStorage.setItem(DARK_MODE_STORAGE_KEY, String(next));
      return next;
    });
  }, []);

  // Re-apply on every darkMode change AND after each Authenticator
  // re-render (which triggers this component to re-render, running the
  // effect). useLayoutEffect fires before browser paint so the class
  // change is atomic with the render.
  useLayoutEffect(() => {
    applyDarkModeClasses(darkMode);
  }, [darkMode]);

  return (
    <Authenticator>
      {({ signOut, user }) => (
        <>
          <TopNavigation
            identity={{
              href: "/",
              title: "IAM Security Assistant",
            }}
            utilities={[
              {
                type: "button",
                text: darkMode ? "Light Mode" : "Dark Mode",
                iconName: darkMode ? "status-positive" : "status-stopped",
                onClick: toggleDarkMode,
              },
              {
                type: "button",
                text: user?.username || "User",
                iconName: "user-profile",
              },
              {
                type: "button",
                text: "Sign out",
                onClick: signOut,
              },
            ]}
          />
          <AppLayout
            content={<ChatInterface />}
            navigationHide={true}
            toolsHide={true}
          />
          <WelcomeModal />
        </>
      )}
    </Authenticator>
  );
}

export default App;
