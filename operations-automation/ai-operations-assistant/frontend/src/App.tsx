import { useState, useEffect, useCallback } from 'react';
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import {
  CognitoIdentityProviderClient,
  InitiateAuthCommand,
  AuthFlowType,
} from '@aws-sdk/client-cognito-identity-provider';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import SideNavigation, { SideNavigationProps } from '@cloudscape-design/components/side-navigation';
import AppLayout from '@cloudscape-design/components/app-layout';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Alert from '@cloudscape-design/components/alert';
import ChatInterface from './components/ChatInterface';
import PromptTemplatePanel from './components/PromptTemplatePanel';
import KnowledgeManager from './components/KnowledgeManager';
import UserPreferences from './components/UserPreferences';
import ConversationHistory from './components/ConversationHistory';
import AccountSelector from './components/AccountSelector';

// ---------------------------------------------------------------------------
// Environment configuration — injected by Vite at build time
// ---------------------------------------------------------------------------
const config = {
  agentRuntimeArn: import.meta.env.VITE_AGENT_RUNTIME_ARN,
  region: import.meta.env.VITE_REGION,
  userPoolId: import.meta.env.VITE_USER_POOL_ID,
  userPoolClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID,
  identityPoolId: import.meta.env.VITE_IDENTITY_POOL_ID,
};

// ---------------------------------------------------------------------------
// Cognito client
// ---------------------------------------------------------------------------
const cognitoClient = new CognitoIdentityProviderClient({
  region: config.region || 'us-east-1',
});

// ---------------------------------------------------------------------------
// Page components
// ---------------------------------------------------------------------------
function ChatPage({
  user,
  accountContext,
  onSessionExpired,
}: {
  user: { username: string; password: string; idToken: string } | null;
  accountContext?: string;
  onSessionExpired?: () => void;
}) {
  const navigate = useNavigate();

  const handleSelectConversation = (conversationId: string) => {
    // Store selected conversation so ChatInterface can load it
    sessionStorage.setItem('goat_active_conversation', conversationId);
    navigate('/');
  };

  if (!user) {
    return (
      <Box padding="l">
        <Box variant="p">Please sign in to use the chat.</Box>
      </Box>
    );
  }
  return (
    <SpaceBetween size="l">
      <Box padding="l">
        <ChatInterface
          agentRuntimeArn={config.agentRuntimeArn}
          idToken={user.idToken}
          region={config.region || 'us-east-1'}
          accountContext={accountContext || undefined}
          onSessionExpired={onSessionExpired}
        />
      </Box>
      <Box padding="l">
        <ConversationHistory
          userId={user.username}
          onSelect={handleSelectConversation}
        />
      </Box>
    </SpaceBetween>
  );
}

function TemplatesPage() {
  const navigate = useNavigate();

  const handleTemplateSubmit = (filledPrompt: string) => {
    // Store the filled prompt so the Chat page can pick it up
    sessionStorage.setItem('goat_pending_prompt', filledPrompt);
    navigate('/');
  };

  return (
    <Box padding="l">
      <PromptTemplatePanel onSubmit={handleTemplateSubmit} />
    </Box>
  );
}

function KnowledgePage() {
  return (
    <Box padding="l">
      <KnowledgeManager />
    </Box>
  );
}

function SettingsPage({
  userId,
  targetAccount,
  onAccountChange,
}: {
  userId: string;
  targetAccount: string;
  onAccountChange: (accountId: string) => void;
}) {
  const [prefsVisible, setPrefsVisible] = useState(false);

  return (
    <Box padding="l">
      <SpaceBetween size="l">
        <Box variant="h1">Settings</Box>
        <Box variant="p">
          Configure default account, preferred templates, and display
          preferences.
        </Box>

        <AccountSelector
          selectedAccountId={targetAccount}
          onChange={onAccountChange}
        />

        <Button variant="primary" onClick={() => setPrefsVisible(true)}>
          Edit preferences
        </Button>

        <UserPreferences
          userId={userId}
          visible={prefsVisible}
          onDismiss={() => setPrefsVisible(false)}
        />
      </SpaceBetween>
    </Box>
  );
}


// ---------------------------------------------------------------------------
// Sign-in modal component (Cognito USER_PASSWORD_AUTH flow)
// ---------------------------------------------------------------------------
interface SignInModalProps {
  visible: boolean;
  onSignIn: (idToken: string, username: string, password: string) => void;
}

function SignInModal({ visible, onSignIn }: SignInModalProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSignIn = async () => {
    setError('');
    setLoading(true);
    try {
      const command = new InitiateAuthCommand({
        AuthFlow: AuthFlowType.USER_PASSWORD_AUTH,
        ClientId: config.userPoolClientId,
        AuthParameters: {
          USERNAME: username,
          PASSWORD: password,
        },
      });
      const response = await cognitoClient.send(command);
      const idToken = response.AuthenticationResult?.IdToken;
      if (!idToken) {
        throw new Error('Authentication succeeded but no ID token was returned.');
      }
      onSignIn(idToken, username, password);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Authentication failed.';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal visible={visible} header="Sign in to G.O.A.T." closeAriaLabel="Close">
      <SpaceBetween size="l">
        {error && <Alert type="error">{error}</Alert>}
        <FormField label="Username">
          <Input
            value={username}
            onChange={({ detail }) => setUsername(detail.value)}
            placeholder="Enter your username"
          />
        </FormField>
        <FormField label="Password">
          <Input
            value={password}
            type="password"
            onChange={({ detail }) => setPassword(detail.value)}
            placeholder="Enter your password"
          />
        </FormField>
        <Button variant="primary" loading={loading} onClick={handleSignIn}>
          Sign in
        </Button>
      </SpaceBetween>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Navigation items
// ---------------------------------------------------------------------------
const NAV_ITEMS: SideNavigationProps.Item[] = [
  { type: 'link', text: 'Chat', href: '/' },
  { type: 'link', text: 'Templates', href: '/templates' },
  { type: 'link', text: 'Knowledge', href: '/knowledge' },
  { type: 'link', text: 'Settings', href: '/settings' },
];

// ---------------------------------------------------------------------------
// App root
// ---------------------------------------------------------------------------
export default function App() {
  const navigate = useNavigate();
  const location = useLocation();

  const [user, setUser] = useState<{ username: string; password: string; idToken: string } | null>(null);
  const [showSignIn, setShowSignIn] = useState(false);
  const [targetAccount, setTargetAccount] = useState('');

  // On mount, check for a stored session
  useEffect(() => {
    const stored = sessionStorage.getItem('goat_user');
    if (stored) {
      try {
        setUser(JSON.parse(stored));
      } catch {
        sessionStorage.removeItem('goat_user');
        setShowSignIn(true);
      }
    } else {
      setShowSignIn(true);
    }
  }, []);

  const handleSignIn = useCallback((idToken: string, username: string, password: string) => {
    const session = { username, password, idToken };
    sessionStorage.setItem('goat_user', JSON.stringify(session));
    setUser(session);
    setShowSignIn(false);
  }, []);

  const handleSignOut = useCallback(() => {
    sessionStorage.removeItem('goat_user');
    setUser(null);
    setShowSignIn(true);
  }, []);

  return (
    <>
      {/* Cognito sign-in modal */}
      <SignInModal visible={showSignIn} onSignIn={handleSignIn} />

      {/* Top navigation bar */}
      <TopNavigation
        identity={{
          href: '/',
          title: 'G.O.A.T.',
          logo: { src: '', alt: 'G.O.A.T.' },
        }}
        utilities={[
          ...(user
            ? [
                {
                  type: 'menu-dropdown' as const,
                  text: user.username,
                  iconName: 'user-profile' as const,
                  items: [{ id: 'signout', text: 'Sign out' }],
                  onItemClick: () => handleSignOut(),
                },
              ]
            : [
                {
                  type: 'button' as const,
                  text: 'Sign in',
                  onClick: () => setShowSignIn(true),
                },
              ]),
        ]}
      />

      {/* Main layout */}
      <AppLayout
        navigation={
          <SideNavigation
            header={{ text: 'G.O.A.T.', href: '/' }}
            activeHref={location.pathname}
            items={NAV_ITEMS}
            onFollow={(event) => {
              event.preventDefault();
              navigate(event.detail.href);
            }}
          />
        }
        content={
          <Routes>
            <Route path="/" element={<ChatPage user={user} accountContext={targetAccount} onSessionExpired={handleSignOut} />} />
            <Route path="/templates" element={<TemplatesPage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route
              path="/settings"
              element={
                <SettingsPage
                  userId={user?.username ?? ''}
                  targetAccount={targetAccount}
                  onAccountChange={setTargetAccount}
                />
              }
            />
          </Routes>
        }
        toolsHide
      />
    </>
  );
}
