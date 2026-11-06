import { ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  AppLayout as CloudscapeAppLayout,
  SideNavigation,
  TopNavigation,
} from '@cloudscape-design/components';
import { UserInfo } from '../App';

interface AppLayoutProps {
  children: ReactNode;
  userInfo: UserInfo | null;
  onSignOut?: () => void;
}

export default function AppLayout({ children, userInfo, onSignOut }: AppLayoutProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const isAdmin = userInfo?.groups.includes('finops-admin');
  const isChampion = isAdmin || userInfo?.groups.includes('champion');

  const navItems = [
    { type: 'link' as const, text: 'Dashboard', href: '/dashboard' },
    { type: 'link' as const, text: 'Findings Backlog', href: '/findings' },
    { type: 'link' as const, text: 'Leaderboard', href: '/leaderboard' },
    { type: 'link' as const, text: 'Learnings', href: '/learnings' },
    { type: 'divider' as const },
    ...(isAdmin ? [
      { type: 'link' as const, text: 'Teams', href: '/teams' },
      { type: 'link' as const, text: 'Scoping Rules', href: '/scoping' },
    ] : []),
  ];

  const getRoleText = (): string => {
    if (isAdmin) return 'Admin';
    if (isChampion) return 'Champion';
    return 'Viewer';
  };

  return (
    <>
      <TopNavigation
        identity={{
          href: '/dashboard',
          title: 'FinOps Gamification Console',
          logo: {
            src: '/favicon.svg',
            alt: 'FinOps',
          },
        }}
        utilities={[
          {
            type: 'menu-dropdown',
            text: `${userInfo?.name || 'User'} (${getRoleText()})`,
            description: userInfo?.email,
            iconName: 'user-profile',
            items: [
              { id: 'profile', text: 'Profile', disabled: true },
              { id: 'signout', text: 'Sign out' },
            ],
            onItemClick: ({ detail }) => {
              if (detail.id === 'signout' && onSignOut) {
                onSignOut();
              }
            },
          },
        ]}
      />
      <CloudscapeAppLayout
        navigation={
          <SideNavigation
            header={{
              text: 'Navigation',
              href: '/dashboard',
            }}
            items={navItems}
            activeHref={location.pathname}
            onFollow={(e) => {
              e.preventDefault();
              navigate(e.detail.href);
            }}
          />
        }
        content={children}
        toolsHide
        navigationWidth={240}
        contentType="default"
      />
    </>
  );
}
