import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Authenticator } from '@aws-amplify/ui-react';
import { fetchAuthSession } from 'aws-amplify/auth';
import '@aws-amplify/ui-react/styles.css';

import AppLayout from './components/AppLayout';
import Dashboard from './pages/Dashboard';
import FindingsBacklog from './pages/FindingsBacklog';
import FindingDetail from './pages/FindingDetail';
import Leaderboard from './pages/Leaderboard';
import Teams from './pages/Teams';
import ScopingRules from './pages/ScopingRules';
import Learnings from './pages/Learnings';

export interface UserInfo {
  userId: string;
  email: string;
  name: string;
  groups: string[];
}

// Local preview mode: bypasses Cognito auth so the UI can be viewed without a
// deployed backend. Enabled only via VITE_MOCK_MODE=true (frontend/.env.local,
// gitignored). Never active in a real deployment.
const MOCK_MODE = import.meta.env.VITE_MOCK_MODE === 'true';

const MOCK_USER_INFO: UserInfo = {
  userId: 'mock-admin-001',
  email: 'admin@example.com',
  name: 'Demo Admin',
  groups: ['finops-admin'],
};

function App() {
  if (MOCK_MODE) {
    return <AuthenticatedApp signOut={() => window.alert('Sign out disabled in local preview mode')} />;
  }

  return (
    <Authenticator>
      {({ signOut, user }) => (
        <AuthenticatedApp signOut={signOut} user={user} />
      )}
    </Authenticator>
  );
}

interface AuthenticatedAppProps {
  signOut?: () => void;
  user?: { username?: string; userId?: string };
}

function AuthenticatedApp({ signOut, user }: AuthenticatedAppProps) {
  const [userInfo, setUserInfo] = useState<UserInfo | null>(MOCK_MODE ? MOCK_USER_INFO : null);
  const [loading, setLoading] = useState(!MOCK_MODE);

  useEffect(() => {
    if (MOCK_MODE) {
      return;
    }
    async function loadUserInfo() {
      try {
        const session = await fetchAuthSession();
        const idToken = session.tokens?.idToken;
        
        if (idToken) {
          const payload = idToken.payload;
          setUserInfo({
            userId: (payload.sub as string) || '',
            email: (payload.email as string) || '',
            name: `${payload.given_name || ''} ${payload.family_name || ''}`.trim() || 'User',
            groups: (payload['cognito:groups'] as string[]) || [],
          });
        }
      } catch (error) {
        console.error('Failed to load user info:', error);
      } finally {
        setLoading(false);
      }
    }
    
    loadUserInfo();
  }, [user]);

  if (loading) {
    return <div>Loading...</div>;
  }

  return (
    <BrowserRouter>
      <AppLayout userInfo={userInfo} onSignOut={signOut}>
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard userInfo={userInfo} />} />
          <Route path="/findings" element={<FindingsBacklog userInfo={userInfo} />} />
          <Route path="/findings/:findingId" element={<FindingDetail userInfo={userInfo} />} />
          <Route path="/leaderboard" element={<Leaderboard />} />
          <Route path="/teams" element={<Teams userInfo={userInfo} />} />
          <Route path="/scoping" element={<ScopingRules userInfo={userInfo} />} />
          <Route path="/learnings" element={<Learnings />} />
        </Routes>
      </AppLayout>
    </BrowserRouter>
  );
}

export default App;
