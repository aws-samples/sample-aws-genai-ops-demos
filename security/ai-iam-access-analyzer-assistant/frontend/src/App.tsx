import { useState, useEffect, useCallback } from "react";
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

// Apply theme immediately on load (before React renders) to prevent flash
const savedMode = localStorage.getItem("iam-analyzer-dark-mode");
const initialDark = savedMode !== null
  ? savedMode === "true"
  : window.matchMedia("(prefers-color-scheme: dark)").matches;
applyMode(initialDark ? Mode.Dark : Mode.Light);

function App() {
  const [darkMode, setDarkMode] = useState(initialDark);

  const toggleDarkMode = useCallback(() => {
    setDarkMode((prev) => {
      const next = !prev;
      applyMode(next ? Mode.Dark : Mode.Light);
      localStorage.setItem("iam-analyzer-dark-mode", String(next));
      return next;
    });
  }, []);

  // Re-apply on mount in case Authenticator re-renders
  useEffect(() => {
    applyMode(darkMode ? Mode.Dark : Mode.Light);
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
