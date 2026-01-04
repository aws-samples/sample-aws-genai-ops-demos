import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import AppLayout from '@cloudscape-design/components/app-layout';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import SideNavigation from '@cloudscape-design/components/side-navigation';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import AuthModal from './AuthModal';
import { getCurrentUser, signOut, AuthUser } from './auth';
import Dashboard from './pages/Dashboard';
import Services from './pages/Services';
import Deprecations from './pages/Deprecations';
import Timeline from './pages/Timeline';

function AppContent() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    checkAuth();
  }, []);

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
              { type: "link", text: "Dashboard", href: "/dashboard" },
              { type: "link", text: "Services", href: "/services" },
              { type: "link", text: "Deprecations", href: "/deprecations" },
              { type: "link", text: "Timeline", href: "/timeline" },
              { type: "divider" },
              {
                type: "link",
                text: "Documentation",
                href: "https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-agentcore.html",
                external: true
              }
            ]}
          />
        }
        toolsHide={true}
        disableContentPaddings
        contentType="default"
        content={
          <ContentLayout defaultPadding>
            <Grid
              gridDefinition={[
                { colspan: { default: 12, xs: 0, s: 0, m: 1 } },
                { colspan: { default: 12, xs: 12, s: 12, m: 10 } },
                { colspan: { default: 12, xs: 0, s: 0, m: 1 } }
              ]}
            >
              <div></div>
              <div>
                {!user ? (
                  <Box textAlign="center" padding="xxl">
                    <Box variant="h1" padding={{ bottom: 's' }}>
                      Welcome to AWS Services Lifecycle Tracker
                    </Box>
                    <Box variant="p" padding={{ bottom: 'm' }} color="text-body-secondary">
                      Please sign in to access the admin interface
                    </Box>
                    <button
                      onClick={() => setShowAuthModal(true)}
                      style={{
                        padding: '10px 20px',
                        fontSize: '16px',
                        cursor: 'pointer',
                        backgroundColor: '#0972d3',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px'
                      }}
                    >
                      Sign In
                    </button>
                  </Box>
                ) : (
                  <Routes>
                    <Route path="/dashboard" element={<Dashboard />} />
                    <Route path="/services" element={<Services />} />
                    <Route path="/deprecations" element={<Deprecations />} />
                    <Route path="/timeline" element={<Timeline />} />
                    <Route path="/" element={<Navigate to="/dashboard" replace />} />
                  </Routes>
                )}
              </div>
              <div></div>
            </Grid>
          </ContentLayout>
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
