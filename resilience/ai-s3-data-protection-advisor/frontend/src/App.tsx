import { useCallback, useEffect, useState } from 'react';
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import AppLayout from '@cloudscape-design/components/app-layout';
import Box from '@cloudscape-design/components/box';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import SideNavigation, { SideNavigationProps } from '@cloudscape-design/components/side-navigation';
import Spinner from '@cloudscape-design/components/spinner';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import { setUnauthorizedHandler } from './api';
import { currentEmail, signOut } from './auth';
import RunPage from './pages/RunPage';
import RunsPage from './pages/RunsPage';
import SamplePage from './pages/SamplePage';
import SignIn from './pages/SignIn';

const NAV_ITEMS: SideNavigationProps.Item[] = [
  { type: 'link', text: 'Runs', href: '/' },
  { type: 'link', text: 'Sample report', href: '/sample' },
  { type: 'divider' },
  {
    type: 'link',
    text: 'Documentation',
    href: 'https://github.com/aws-samples/sample-aws-genai-ops-demos/tree/main/resilience/ai-s3-data-protection-advisor',
    external: true,
  },
];

function NotFound() {
  return (
    <ContentLayout header={<Header variant="h1">Page not found.</Header>}>
      <Box>Choose a page from the navigation.</Box>
    </ContentLayout>
  );
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  const refresh = useCallback(async () => {
    setEmail(await currentEmail());
    setChecking(false);
  }, []);

  useEffect(() => {
    void refresh();
    setUnauthorizedHandler(() => setEmail(null));
    return () => setUnauthorizedHandler(null);
  }, [refresh]);

  if (checking) {
    return (
      <Box padding="xxl" textAlign="center">
        <Spinner size="large" />
      </Box>
    );
  }
  if (!email) return <SignIn onSignedIn={() => void refresh()} />;

  return (
    <>
      <div id="top-nav">
        <TopNavigation
          identity={{ href: '/', title: 'S3 data protection advisor' }}
          utilities={[
            {
              type: 'menu-dropdown',
              text: email,
              iconName: 'user-profile',
              items: [{ id: 'signout', text: 'Sign out' }],
              onItemClick: ({ detail }) => {
                if (detail.id === 'signout') {
                  signOut();
                  setEmail(null);
                  navigate('/');
                }
              },
            },
          ]}
        />
      </div>
      <AppLayout
        headerSelector="#top-nav"
        toolsHide
        navigation={
          <SideNavigation
            activeHref={location.pathname === '/sample' ? '/sample' : '/'}
            items={NAV_ITEMS}
            onFollow={(e) => {
              if (!e.detail.external) {
                e.preventDefault();
                navigate(e.detail.href);
              }
            }}
          />
        }
        content={
          <Routes>
            <Route path="/" element={<RunsPage />} />
            <Route path="/runs/:id" element={<RunPage />} />
            <Route path="/sample" element={<SamplePage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        }
      />
    </>
  );
}
