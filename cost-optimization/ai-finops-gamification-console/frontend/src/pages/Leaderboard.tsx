import { useState, useEffect } from 'react';
import {
  Table,
  Header,
  SpaceBetween,
  Box,
  Badge,
  Select,
  SelectProps,
  ColumnLayout,
  Spinner,
  Alert,
  Container,
  Icon,
} from '@cloudscape-design/components';
import { LeaderboardEntry } from '../types';
import { getLeaderboard } from '../services/api';

function getMonthOptions(): SelectProps.Option[] {
  const options: SelectProps.Option[] = [];
  const now = new Date();
  
  for (let i = 0; i < 12; i++) {
    const date = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
    const label = date.toLocaleDateString('en-US', { year: 'numeric', month: 'long' });
    options.push({ value, label });
  }
  
  return options;
}

function getRankBadge(rank: number) {
  if (rank === 1) return <Badge color="green">1st</Badge>;
  if (rank === 2) return <Badge color="blue">2nd</Badge>;
  if (rank === 3) return <Badge color="grey">3rd</Badge>;
  return <Box>{rank}th</Box>;
}

function getTrophyIcon(rank: number) {
  if (rank === 1) return <Icon name="status-positive" variant="success" />;
  if (rank === 2) return <Icon name="status-info" variant="link" />;
  if (rank === 3) return <Icon name="status-info" variant="subtle" />;
  return null;
}

export default function Leaderboard() {
  const monthOptions = getMonthOptions();
  const [selectedMonth, setSelectedMonth] = useState<SelectProps.Option>(monthOptions[0]);
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadLeaderboard() {
      setLoading(true);
      try {
        const data = await getLeaderboard(selectedMonth.value);
        setLeaderboard(data.leaderboard);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load leaderboard');
      } finally {
        setLoading(false);
      }
    }
    loadLeaderboard();
  }, [selectedMonth]);

  const topPerformers = leaderboard.slice(0, 3);
  const totalSavings = leaderboard.reduce((sum, entry) => sum + entry.totalSavingsUsd, 0);
  const totalPoints = leaderboard.reduce((sum, entry) => sum + entry.totalPoints, 0);
  const totalFindings = leaderboard.reduce((sum, entry) => sum + entry.findingsAccepted + entry.findingsRejected, 0);

  if (loading) {
    return (
      <Box textAlign="center" padding="xxl">
        <Spinner size="large" />
        <Box variant="p" margin={{ top: 's' }}>Loading leaderboard...</Box>
      </Box>
    );
  }

  if (error) {
    return (
      <Alert type="error" header="Error loading leaderboard">
        {error}
      </Alert>
    );
  }

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Track your progress and compete with your team"
        actions={
          <Select
            selectedOption={selectedMonth}
            onChange={({ detail }) => setSelectedMonth(detail.selectedOption)}
            options={monthOptions}
          />
        }
      >
        Leaderboard
      </Header>

      <ColumnLayout columns={4} variant="text-grid">
        <Box>
          <Box variant="awsui-key-label">Total Participants</Box>
          <Box fontSize="display-l">{leaderboard.length}</Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Total Savings</Box>
          <Box fontSize="display-l" color="text-status-success">
            ${totalSavings.toLocaleString()}
          </Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Total Points</Box>
          <Box fontSize="display-l" color="text-status-info">
            {totalPoints.toLocaleString()}
          </Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Findings Processed</Box>
          <Box fontSize="display-l">{totalFindings}</Box>
        </Box>
      </ColumnLayout>

      {topPerformers.length > 0 && (
        <Container header={<Header variant="h2">Top Performers</Header>}>
          <ColumnLayout columns={3}>
            {topPerformers.map((entry, index) => (
              <Box key={entry.userId} textAlign="center" padding="l">
                <Box fontSize="display-l">{getTrophyIcon(index + 1)}</Box>
                <Box fontSize="heading-l" fontWeight="bold" margin={{ top: 's' }}>
                  {entry.userName}
                </Box>
                <Box margin={{ top: 'xs' }}>
                  {getRankBadge(index + 1)}
                </Box>
                <ColumnLayout columns={2} variant="text-grid">
                  <Box margin={{ top: 'm' }}>
                    <Box variant="awsui-key-label">Points</Box>
                    <Box fontSize="heading-m" color="text-status-info">
                      {entry.totalPoints.toLocaleString()}
                    </Box>
                  </Box>
                  <Box margin={{ top: 'm' }}>
                    <Box variant="awsui-key-label">Savings</Box>
                    <Box fontSize="heading-m" color="text-status-success">
                      ${entry.totalSavingsUsd.toLocaleString()}
                    </Box>
                  </Box>
                </ColumnLayout>
              </Box>
            ))}
          </ColumnLayout>
        </Container>
      )}

      <Table
        columnDefinitions={[
          {
            id: 'rank',
            header: 'Rank',
            cell: item => getRankBadge(item.rank || 0),
            width: 80,
          },
          {
            id: 'name',
            header: 'Name',
            cell: item => (
              <SpaceBetween direction="horizontal" size="xs">
                {getTrophyIcon(item.rank || 0)}
                <Box fontWeight={item.rank && item.rank <= 3 ? 'bold' : 'normal'}>
                  {item.userName}
                </Box>
              </SpaceBetween>
            ),
            sortingField: 'userName',
          },
          {
            id: 'points',
            header: 'Points',
            cell: item => (
              <Box color="text-status-info" fontWeight="bold">
                {item.totalPoints.toLocaleString()}
              </Box>
            ),
            sortingField: 'totalPoints',
            width: 120,
          },
          {
            id: 'savings',
            header: 'Savings',
            cell: item => (
              <Box color="text-status-success" fontWeight="bold">
                ${item.totalSavingsUsd.toLocaleString()}
              </Box>
            ),
            sortingField: 'totalSavingsUsd',
            width: 130,
          },
          {
            id: 'accepted',
            header: 'Accepted',
            cell: item => item.findingsAccepted,
            sortingField: 'findingsAccepted',
            width: 100,
          },
          {
            id: 'rejected',
            header: 'Rejected',
            cell: item => item.findingsRejected,
            sortingField: 'findingsRejected',
            width: 100,
          },
          {
            id: 'lastActivity',
            header: 'Last Activity',
            cell: item => item.lastActivity ? new Date(item.lastActivity).toLocaleDateString() : '-',
            width: 130,
          },
        ]}
        items={leaderboard.map((entry, index) => ({ ...entry, rank: index + 1 }))}
        loading={loading}
        loadingText="Loading leaderboard..."
        empty={
          <Box textAlign="center" padding="l">
            <Box variant="h3">No data yet</Box>
            <Box variant="p">Be the first to review findings and earn points.</Box>
          </Box>
        }
        variant="full-page"
        stickyHeader
        sortingDisabled
      />
    </SpaceBetween>
  );
}
