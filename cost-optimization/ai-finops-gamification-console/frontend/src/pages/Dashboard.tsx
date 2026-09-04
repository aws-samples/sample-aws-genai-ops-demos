import { useState, useEffect } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  ColumnLayout,
  ProgressBar,
  StatusIndicator,
  Link,
  Spinner,
  Alert,
  Cards,
  Button,
} from '@cloudscape-design/components';
import { useNavigate } from 'react-router-dom';
import { UserInfo } from '../App';
import { DashboardStats } from '../types';
import { getDashboardStats } from '../services/api';

interface DashboardProps {
  userInfo: UserInfo | null;
}

export default function Dashboard({ userInfo }: DashboardProps) {
  const navigate = useNavigate();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadStats() {
      try {
        const data = await getDashboardStats();
        setStats(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load dashboard');
      } finally {
        setLoading(false);
      }
    }
    loadStats();
  }, []);

  if (loading) {
    return (
      <Box textAlign="center" padding="xxl">
        <Spinner size="large" />
        <Box variant="p" margin={{ top: 's' }}>Loading dashboard...</Box>
      </Box>
    );
  }

  if (error) {
    return (
      <Alert type="error" header="Error loading dashboard">
        {error}
      </Alert>
    );
  }

  const totalFindings = stats?.findings.total || 0;
  const acceptanceRate = totalFindings > 0 
    ? Math.round((stats?.findings.accepted || 0) / totalFindings * 100) 
    : 0;

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description={`Welcome back, ${userInfo?.name || 'User'}! Here's your FinOps overview for ${stats?.month || 'this month'}.`}
      >
        FinOps Dashboard
      </Header>

      <ColumnLayout columns={4} variant="text-grid">
        <Box>
          <Box variant="awsui-key-label">Pending Findings</Box>
          <Link fontSize="display-l" href="/findings" onFollow={(e) => { e.preventDefault(); navigate('/findings?status=pending'); }}>
            {stats?.findings.pending || 0}
          </Link>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Monthly Savings</Box>
          <Box fontSize="display-l" fontWeight="bold" color="text-status-success">
            ${(stats?.savings.totalUsd || 0).toLocaleString()}
          </Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Your Points</Box>
          <Box fontSize="display-l" fontWeight="bold" color="text-status-info">
            {stats?.user.points || 0}
          </Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Active Teams</Box>
          <Box fontSize="display-l">
            {stats?.teams.count || 0}
          </Box>
        </Box>
      </ColumnLayout>

      <ColumnLayout columns={2}>
        <Container
          header={<Header variant="h2">Findings Overview</Header>}
        >
          <SpaceBetween size="m">
            <ColumnLayout columns={3} variant="text-grid">
              <Box>
                <Box variant="awsui-key-label">Accepted</Box>
                <Box fontSize="heading-xl">
                  <StatusIndicator type="success">{stats?.findings.accepted || 0}</StatusIndicator>
                </Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Rejected</Box>
                <Box fontSize="heading-xl">
                  <StatusIndicator type="stopped">{stats?.findings.rejected || 0}</StatusIndicator>
                </Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Pending</Box>
                <Box fontSize="heading-xl">
                  <StatusIndicator type="pending">{stats?.findings.pending || 0}</StatusIndicator>
                </Box>
              </Box>
            </ColumnLayout>
            
            <Box>
              <Box variant="awsui-key-label" margin={{ bottom: 'xs' }}>Acceptance Rate</Box>
              <ProgressBar
                value={acceptanceRate}
                label={`${acceptanceRate}% of findings accepted`}
                status={acceptanceRate >= 70 ? 'success' : acceptanceRate >= 40 ? 'in-progress' : 'error'}
              />
            </Box>

            <Button variant="primary" onClick={() => navigate('/findings')}>
              View All Findings
            </Button>
          </SpaceBetween>
        </Container>

        <Container
          header={<Header variant="h2">Your Performance</Header>}
        >
          <SpaceBetween size="m">
            <ColumnLayout columns={2} variant="text-grid">
              <Box>
                <Box variant="awsui-key-label">Findings Accepted</Box>
                <Box fontSize="heading-xl">{stats?.user.findingsAccepted || 0}</Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Findings Rejected</Box>
                <Box fontSize="heading-xl">{stats?.user.findingsRejected || 0}</Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Your Savings</Box>
                <Box fontSize="heading-xl" color="text-status-success">
                  ${(stats?.user.savingsUsd || 0).toLocaleString()}
                </Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Points This Month</Box>
                <Box fontSize="heading-xl" color="text-status-info">
                  {stats?.user.points || 0}
                </Box>
              </Box>
            </ColumnLayout>

            <Button onClick={() => navigate('/leaderboard')}>
              View Leaderboard
            </Button>
          </SpaceBetween>
        </Container>
      </ColumnLayout>

      <Container
        header={
          <Header
            variant="h2"
            description="Quick access to key actions"
          >
            Quick Actions
          </Header>
        }
      >
        <Cards
          cardDefinition={{
            header: item => item.title,
            sections: [
              {
                id: 'description',
                content: item => item.description,
              },
            ],
          }}
          items={[
            {
              title: 'Review Pending Findings',
              description: `${stats?.findings.pending || 0} findings awaiting review`,
              href: '/findings?status=pending',
            },
            {
              title: 'View Learnings',
              description: 'See patterns from past decisions',
              href: '/learnings',
            },
            {
              title: 'Check Leaderboard',
              description: 'See where you rank this month',
              href: '/leaderboard',
            },
          ]}
          cardsPerRow={[{ cards: 3 }]}
          onSelectionChange={({ detail }) => {
            const item = detail.selectedItems[0];
            if (item?.href) navigate(item.href);
          }}
          selectionType="single"
        />
      </Container>
    </SpaceBetween>
  );
}
