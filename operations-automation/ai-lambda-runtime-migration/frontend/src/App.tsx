import { useState, useEffect } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import AppLayout from '@cloudscape-design/components/app-layout';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import SideNavigation, { SideNavigationProps } from '@cloudscape-design/components/side-navigation';
import SplitPanel from '@cloudscape-design/components/split-panel';
import Button from '@cloudscape-design/components/button';
import Box from '@cloudscape-design/components/box';
import AuthModal from './AuthModal';
import { getCurrentUser, signOut, AuthUser } from './auth';
import { SplitPanelProvider, useSplitPanel } from './SplitPanelContext';

import MyFunctions from './pages/MyFunctions';
import ScanLogPage from './pages/ScanLogPage';
import StatisticsPage from './pages/StatisticsPage';
import HowItWorksPage from './pages/HowItWorksPage';
import SettingsPage from './pages/SettingsPage';

const NAV_ITEMS: SideNavigationProps.Item[] = [
  { type: 'link', text: 'My functions', href: '/' },
  { type: 'link', text: 'Activity log', href: '/scan-log' },
  { type: 'link', text: 'Statistics', href: '/statistics' },
  { type: 'link', text: 'How it works', href: '/how-it-works' },
  { type: 'divider' },
  { type: 'link', text: 'Settings', href: '/settings' },
  { type: 'divider' },
  {
    type: 'link',
    text: 'Documentation',
    href: 'https://github.com/aws-samples/sample-aws-genai-ops-demos/tree/main/operations-automation/ai-lambda-runtime-migration',
    external: true,
  },
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
  const [splitPanelPosition, setSplitPanelPosition] = useState<'bottom' | 'side'>('side');
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
          identity={{ href: '#', title: 'AWS Lambda Runtime Migration' }}
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
          title: 'AWS Lambda Runtime Migration',
          onFollow: (e) => { e.preventDefault(); navigate('/'); },
        }}
        utilities={[
          {
            type: 'button',
            text: user ? user.email : 'Sign In',
            iconName: user ? 'user-profile' : 'lock-private',
            onClick: () => { if (user) handleSignOut(); else setShowAuthModal(true); },
          },
        ]}
        i18nStrings={{ overflowMenuTriggerText: 'More' }}
      />
      <AppLayout
        navigation={
          <SideNavigation
            activeHref={location.pathname}
            items={NAV_ITEMS}
            onFollow={(e) => { if (!e.detail.external) { e.preventDefault(); navigate(e.detail.href); } }}
          />
        }
        navigationOpen={navOpen}
        onNavigationChange={({ detail }) => setNavOpen(detail.open)}
        toolsHide
        contentType="table"
        maxContentWidth={Number.MAX_VALUE}
        stickyNotifications
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
              <Box variant="h1" padding={{ bottom: 's' }}>AWS Lambda Runtime Migration</Box>
              <Box variant="p" padding={{ bottom: 'm' }} color="text-body-secondary">
                Please sign in to access the migration tool
              </Box>
              <Button variant="primary" iconName="lock-private" onClick={() => setShowAuthModal(true)}>Sign In</Button>
            </Box>
          ) : (
            <Routes>
              <Route path="/" element={<MyFunctions />} />
              <Route path="/scan-log" element={<ScanLogPage />} />
              <Route path="/statistics" element={<StatisticsPage />} />
              <Route path="/how-it-works" element={<HowItWorksPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          )
        }
      />
    </>
  );
}
