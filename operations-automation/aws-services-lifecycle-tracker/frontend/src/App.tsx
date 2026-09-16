import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import AppLayout from '@cloudscape-design/components/app-layout';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import SideNavigation from '@cloudscape-design/components/side-navigation';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Box from '@cloudscape-design/components/box';
import SplitPanel from '@cloudscape-design/components/split-panel';
import Button from '@cloudscape-design/components/button';
import AuthModal from './AuthModal';
import { SplitPanelContext, SplitPanelContent } from './split-panel';
import { HelpContext, HelpContent, helpFor } from './help';
import { getCurrentUser, signOut, AuthUser } from './auth';
import Dashboard from './pages/Dashboard';
import Services from './pages/Services';
import ServiceDetail from './pages/ServiceDetail';
import MyResources from './pages/MyResources';
import Catalog from './pages/Catalog';
import Timeline from './pages/Timeline';
import PlanOfAction from './pages/PlanOfAction';

function AppContent() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(true);
  // Split panel content is set by the current page (see split-panel.tsx)
  const [panel, setPanel] = useState<SplitPanelContent | null>(null);
  // Help panel (AppLayout tools): content follows the route, opened by the Info links
  const [toolsOpen, setToolsOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    checkAuth();
  }, []);
  // Leaving the page closes its panel
  useEffect(() => { setPanel(null); }, [location.pathname]);
  // Every page is a table or a dashboard: use the whole width everywhere
  // (same width as the split panel), no per-page max content width.
  const contentType = location.pathname === '/dashboard' ? 'dashboard' : 'table';

  const checkAuth = async () => {
    try {
      const currentUser = await getCurrentUser();
      setUser(currentUser);
    } catch (err) {
      setUser(null);
    } finally {
      setCheckingAuth(false);
    }
  };

  const handleSignOut = () => {
    signOut();
    setUser(null);
  };

  const handleAuthSuccess = async () => {
    setShowAuthModal(false);
    await checkAuth();
  };

  if (checkingAuth) {
    return (
      <>
        <TopNavigation
          identity={{
            href: "#",
            title: "AWS Services Lifecycle Tracker"
          }}
          utilities={[
            {
              type: "button",
              text: "Loading...",
              iconName: "status-in-progress"
            }
          ]}
        />
        <AppLayout
          navigationHide={true}
          toolsHide={true}
          disableContentPaddings
          contentType="default"
          content={
            <ContentLayout defaultPadding>
              <Box textAlign="center" padding="xxl">
                Loading...
              </Box>
            </ContentLayout>
          }
        />
      </>
    );
  }

  return (
    <>
      <AuthModal
        visible={showAuthModal}
        onDismiss={() => setShowAuthModal(false)}
        onSuccess={handleAuthSuccess}
      />
      <TopNavigation
        identity={{
          href: "#",
          title: "AWS Services Lifecycle Tracker",
          onFollow: (e) => {
            e.preventDefault();
            navigate('/dashboard');
          }
        }}
        utilities={[
          {
            type: "button",
            text: user ? `${user.email}` : "Sign In",
            iconName: user ? "user-profile" : "lock-private",
            onClick: () => {
              if (user) {
                handleSignOut();
              } else {
                setShowAuthModal(true);
              }
            }
          }
        ]}
        i18nStrings={{
          overflowMenuTriggerText: "More",
          overflowMenuTitleText: "All"
        }}
      />
      <AppLayout
        navigation={
          <SideNavigation
            activeHref={location.pathname}
            header={{
              href: "/dashboard",
              text: "Lifecycle Tracker"
            }}
            onFollow={(event) => {
              event.preventDefault();
              navigate(event.detail.href);
            }}
            items={[
              { type: "section", text: "My account", items: [
                { type: "link", text: "My exposure", href: "/dashboard" },
                { type: "link", text: "My resources", href: "/resources" },
                { type: "link", text: "Timeline", href: "/timeline" },
                { type: "link", text: "Plan of Action", href: "/plan-of-action" },
              ] },
              { type: "section", text: "Reference", items: [
                { type: "link", text: "Catalog", href: "/catalog" },
                { type: "link", text: "Sources & coverage", href: "/services" },
              ] },
              { type: "divider" },
              {
                type: "link",
                text: "Documentation",
                href: "https://github.com/aws-samples/sample-aws-genai-ops-demos/tree/main/operations-automation/aws-services-lifecycle-tracker",
                external: true
              }
            ]}
          />
        }
        toolsHide={!user || !helpFor(location.pathname)}
        tools={<HelpContent pathname={location.pathname} />}
        toolsOpen={toolsOpen}
        onToolsChange={({ detail }) => setToolsOpen(detail.open)}
        ariaLabels={{ tools: 'Help panel', toolsToggle: 'Open help panel', toolsClose: 'Close help panel' }}
        contentType={contentType}
        maxContentWidth={Number.MAX_VALUE}
        splitPanelOpen={panel !== null}
        onSplitPanelToggle={({ detail }) => { if (!detail.open) panel?.onClose(); }}
        splitPanel={panel ? (
          <SplitPanel
            header={panel.header}
            closeBehavior="hide"
            i18nStrings={{
              preferencesTitle: 'Split panel preferences', preferencesPositionLabel: 'Position',
              preferencesPositionDescription: 'Choose the default position for the split panel.',
              preferencesPositionSide: 'Side', preferencesPositionBottom: 'Bottom',
              preferencesConfirm: 'Confirm', preferencesCancel: 'Cancel',
              closeButtonAriaLabel: 'Close panel', openButtonAriaLabel: 'Open panel',
              resizeHandleAriaLabel: 'Resize split panel',
            }}
          >
            {panel.content}
          </SplitPanel>
        ) : undefined}
        content={
          <SplitPanelContext.Provider value={setPanel}>
          <HelpContext.Provider value={() => setToolsOpen(true)}>
                {!user ? (
                  <Box textAlign="center" padding="xxl">
                    <Box variant="h1" padding={{ bottom: 's' }}>
                      Welcome to AWS Services Lifecycle Tracker
                    </Box>
                    <Box variant="p" padding={{ bottom: 'm' }} color="text-body-secondary">
                      Please sign in to access the admin interface
                    </Box>
                    <Button variant="primary" iconName="lock-private" onClick={() => setShowAuthModal(true)}>
                      Sign in
                    </Button>
                  </Box>
                ) : (
                  <Routes>
                    <Route path="/dashboard" element={<Dashboard />} />
                    <Route path="/services" element={<Services />} />
                    <Route path="/services/:serviceName" element={<ServiceDetail />} />
                    <Route path="/resources" element={<MyResources />} />
                    <Route path="/catalog" element={<Catalog />} />
                    <Route path="/deprecations" element={<Navigate to="/catalog" replace />} />
                    <Route path="/timeline" element={<Timeline />} />
                    <Route path="/plan-of-action" element={<PlanOfAction />} />
                    <Route path="/" element={<Navigate to="/dashboard" replace />} />
                  </Routes>
                )}
          </HelpContext.Provider>
          </SplitPanelContext.Provider>
        }
      />
    </>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AppContent />
    </BrowserRouter>
  );
}

export default App;
