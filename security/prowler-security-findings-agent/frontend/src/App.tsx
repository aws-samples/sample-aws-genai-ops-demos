import { useEffect, useState } from 'react';
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import {
  AppLayout,
  SideNavigation,
  TopNavigation,
} from '@cloudscape-design/components';
import { applyMode, Mode } from '@cloudscape-design/global-styles';
import Dashboard from './pages/Dashboard';
import Findings from './pages/Findings';
import FindingDetail from './pages/FindingDetail';
import Compliance from './pages/Compliance';
import Investigations from './pages/Investigations';
import Cost from './pages/Cost';
import AuthModal from './AuthModal';
import KeyboardShortcuts from './KeyboardShortcuts';
import { getCurrentUser, signOut, AuthUser } from './auth';

type Theme = 'system' | 'light' | 'dark';

function applyTheme(theme: Theme) {
  const html = document.documentElement;
  html.classList.remove('light-mode', 'dark-mode', 'system-mode');
  html.classList.add(`${theme}-mode`);

  // Compute effective mode for Cloudscape (no 'system' option there).
  let effective: Mode;
  if (theme === 'light') effective = Mode.Light;
  else if (theme === 'dark') effective = Mode.Dark;
  else effective = window.matchMedia('(prefers-color-scheme: light)').matches ? Mode.Light : Mode.Dark;
  applyMode(effective);

  // theme-color meta for mobile chrome
  const meta = document.querySelector('meta[name="theme-color"]') as HTMLMetaElement | null;
  if (meta) meta.content = effective === Mode.Light ? '#f4f6fb' : '#0b0f1a';
}

function getInitialTheme(): Theme {
  // Accessing localStorage can throw a SecurityError in private browsing or
  // when third-party storage is blocked — swallow and fall back to 'system'.
  try {
    const saved = localStorage.getItem('soc-theme');
    if (saved === 'light' || saved === 'dark' || saved === 'system') return saved;
  } catch { /* noop */ }
  return 'system';
}

function saveTheme(theme: Theme) {
  try { localStorage.setItem('soc-theme', theme); } catch { /* noop */ }
}

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const location = useLocation();
  const navigate = useNavigate();

  // Apply theme on mount + whenever it changes + whenever OS pref changes (for system).
  useEffect(() => {
    applyTheme(theme);
    saveTheme(theme);
    if (theme !== 'system') return;
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const listener = () => applyTheme('system');
    mq.addEventListener('change', listener);
    return () => mq.removeEventListener('change', listener);
  }, [theme]);

  useEffect(() => {
    getCurrentUser().then((u) => {
      setUser(u);
      setAuthChecked(true);
    });
  }, []);

  if (!authChecked) return null;
  if (!user) {
    return <AuthModal onAuthenticated={() => getCurrentUser().then(setUser)} />;
  }

  return (
    <>
      <a href="#soc-main" className="soc-skip-link">Skip to main content</a>
      <KeyboardShortcuts />
      <TopNavigation
        identity={{
          href: '/',
          title: 'AI Ops Security Center',
        }}
        utilities={[
          {
            type: 'menu-dropdown',
            text: theme === 'system' ? 'Theme · Auto' : theme === 'light' ? 'Theme · Light' : 'Theme · Dark',
            iconName: theme === 'light' ? 'star' : theme === 'dark' ? 'status-info' : 'settings',
            items: [
              { id: 'system', text: 'System (auto)' },
              { id: 'light',  text: 'Light' },
              { id: 'dark',   text: 'Dark' },
            ],
            onItemClick: (e) => {
              const next = e.detail.id as Theme;
              if (next === 'system' || next === 'light' || next === 'dark') setTheme(next);
            },
          },
          {
            type: 'button',
            text: 'AWS Console',
            external: true,
            href: 'https://console.aws.amazon.com/',
          },
          {
            type: 'menu-dropdown',
            text: user.email || user.username,
            iconName: 'user-profile',
            items: [
              { id: 'signout', text: 'Sign out' },
            ],
            onItemClick: (e) => {
              if (e.detail.id === 'signout') {
                signOut();
                setUser(null);
              }
            },
          },
        ]}
      />
      <AppLayout
        navigationHide={false}
        navigation={
          <SideNavigation
            header={{ href: '/', text: 'Navigation' }}
            activeHref={location.pathname === '/' ? '/' : location.pathname.startsWith('/findings') ? '/findings' : location.pathname}
            onFollow={(e) => {
              if (!e.detail.external) {
                e.preventDefault();
                navigate(e.detail.href);
              }
            }}
            items={[
              { type: 'link', text: 'Dashboard', href: '/' },
              { type: 'link', text: 'Findings', href: '/findings' },
              { type: 'link', text: 'Compliance', href: '/compliance' },
              { type: 'link', text: 'Investigations', href: '/investigations' },
              { type: 'link', text: 'Cost & GenAI telemetry', href: '/cost' },
              { type: 'divider' },
              {
                type: 'link',
                text: 'Prowler docs',
                href: 'https://docs.prowler.com/',
                external: true,
              },
            ]}
          />
        }
        toolsHide
        content={
          <div id="soc-main" tabIndex={-1}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/findings" element={<Findings />} />
              <Route path="/findings/:findingUid" element={<FindingDetail />} />
              <Route path="/compliance" element={<Compliance />} />
              <Route path="/investigations" element={<Investigations />} />
              <Route path="/cost" element={<Cost />} />
            </Routes>
          </div>
        }
      />
    </>
  );
}
