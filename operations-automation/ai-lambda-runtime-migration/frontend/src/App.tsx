import { useState, useEffect } from 'react';
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import AppLayout from '@cloudscape-design/components/app-layout';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import SideNavigation, { SideNavigationProps } from '@cloudscape-design/components/side-navigation';
import SplitPanel from '@cloudscape-design/components/split-panel';
import Box from '@cloudscape-design/components/box';
import AuthModal from './AuthModal';
import { getCurrentUser, signOut, AuthUser } from './auth';
import { SplitPanelProvider, useSplitPanel } from './SplitPanelContext';

import Dashboard from './pages/Dashboard';
import Functions from './pages/Functions';
import FunctionDetail from './pages/FunctionDetail';
import MigrationPlan from './pages/MigrationPlan';

const NAV_ITEMS: SideNavigationProps.Item[] = [
  { type: 'link', text: 'Dashboard', href: '/' },
  { type: 'link', text: 'Functions', href: '/functions' },
  { type: 'link', text: 'Migration Plan', href: '/migration-plan' },
];

export default function App() {
  return (
    <SplitPanelProvider>
      <AppContent />
    </SplitPanelProvider>
  );
}

function AppContent() {
  const navigate = useNavigate();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(true);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [splitPanelPosition, setSplitPanelPosition] = useState<'bottom' | 'side'>('bottom');
  const { state: splitPanelState, closePanel } = useSplitPanel();

  useEffect(() => { checkAuth(); }, []);

  const checkAuth = async () => {
    try {
      const currentUser = await getCurrentUser();
      setUser(currentUser);
    } catch { setUser(null); }
    finally { setCheckingAuth(false); }
  };

  const handleSignOut = () => { signOut(); setUser(null); };
  const handleAuthSuccess = async () => { setShowAuthModal(false); await checkAuth(); };

  if (checkingAuth) {
    return (
      <>
        <TopNavigation
          identity={{ href: '#', title: 'AWS Lambda Functions Migration Assistant' }}
          utilities={[{ type: 'button', text: 'Loading...', iconName: 'status-in-progress' }]}
        />
        <AppLayout navigationHide toolsHide content={<Box textAlign="center" padding="xxl">Loading...</Box>} />
      </>
    );
  }

  return (
    <>
      <AuthModal visible={showAuthModal} onDismiss={() => setShowAuthModal(false)} onSuccess={handleAuthSuccess} />
      <TopNavigation
        identity={{
          href: '/',
          title: 'AWS Lambda Functions Migration Assistant',
          onFollow: (e) => { e.preventDefault(); navigate('/'); },
        }}
        utilities={[{
          type: 'button',
          text: user ? user.email : 'Sign In',
          iconName: user ? 'user-profile' : 'lock-private',
          onClick: () => { if (user) handleSignOut(); else setShowAuthModal(true); },
        }]}
        i18nStrings={{ overflowMenuTriggerText: 'More' }}
      />
      <AppLayout
        navigation={
          <SideNavigation
            activeHref={location.pathname}
            items={NAV_ITEMS}
            onFollow={(e) => { e.preventDefault(); navigate(e.detail.href); }}
          />
        }
        navigationOpen={navOpen}
        onNavigationChange={({ detail }) => setNavOpen(detail.open)}
        toolsHide
        splitPanel={
          splitPanelState.isOpen && splitPanelState.content ? (
            <SplitPanel
              header={splitPanelState.header}
              closeBehavior="hide"
              i18nStrings={{
                preferencesTitle: 'Split panel preferences',
                preferencesPositionLabel: 'Position',
                preferencesPositionDescription: 'Choose the position of the split panel',
                preferencesPositionSide: 'Side',
                preferencesPositionBottom: 'Bottom',
                preferencesConfirm: 'Confirm',
                preferencesCancel: 'Cancel',
                closeButtonAriaLabel: 'Close panel',
                openButtonAriaLabel: 'Open panel',
                resizeHandleAriaLabel: 'Resize panel',
              }}
            >
              {splitPanelState.content}
            </SplitPanel>
          ) : undefined
        }
        splitPanelOpen={splitPanelState.isOpen}
        onSplitPanelToggle={({ detail }) => { if (!detail.open) closePanel(); }}
        splitPanelPreferences={{ position: splitPanelPosition }}
        onSplitPanelPreferencesChange={({ detail }) => setSplitPanelPosition(detail.position as 'bottom' | 'side')}
        content={
          !user ? (
            <Box textAlign="center" padding="xxl">
              <Box variant="h1" padding={{ bottom: 's' }}>AWS Lambda Functions Migration Assistant</Box>
              <Box variant="p" padding={{ bottom: 'm' }} color="text-body-secondary">
                Please sign in to access the migration dashboard
              </Box>
              <button
                onClick={() => setShowAuthModal(true)}
                style={{
                  padding: '10px 20px', fontSize: '16px', cursor: 'pointer',
                  backgroundColor: '#0972d3', color: 'white', border: 'none', borderRadius: '4px',
                }}
              >
                Sign In
              </button>
            </Box>
          ) : (
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/functions" element={<Functions />} />
              <Route path="/functions/:arn" element={<FunctionDetail />} />
              <Route path="/migration-plan" element={<MigrationPlan />} />
            </Routes>
          )
        }
      />
    </>
  );
}
