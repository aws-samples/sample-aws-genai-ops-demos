/**
 * Local-only mock data for previewing the UI without a deployed backend.
 * Used exclusively by api.ts when VITE_MOCK_MODE=true (see frontend/.env.local).
 * Never bundled into a real deployment path since MOCK_MODE gates its usage.
 */
import { Finding, Team, ScopingRule, LeaderboardEntry, DashboardStats, Learning } from '../types';

const mockFindings: Finding[] = [
  {
    findingId: 'f-1001',
    type: 'optimization',
    title: 'Idle EC2 instances in dev account',
    description: '12 EC2 instances in the dev account have had <5% CPU utilization for 30 days. Estimated savings if downsized or stopped: $2,140/month.',
    service: 'AmazonEC2',
    category: 'optimization',
    estimatedSavingsUsd: 2140,
    status: 'pending',
    priority: 'high',
    assignedTeamId: 't-platform',
    accountIds: ['123456789012'],
    tags: ['env:dev'],
    createdAt: '2026-08-25T09:00:00Z',
    source: 'finops-agent',
  },
  {
    findingId: 'f-1002',
    type: 'anomaly',
    title: 'Data transfer spend spike (+34%)',
    description: 'Cross-AZ data transfer costs increased 34% week-over-week, concentrated in the "orders-service" workload.',
    service: 'AmazonEC2',
    category: 'anomaly',
    estimatedSavingsUsd: 890,
    status: 'pending',
    priority: 'critical',
    assignedTeamId: 't-application',
    accountIds: ['123456789012'],
    tags: ['service:orders'],
    createdAt: '2026-08-28T14:30:00Z',
    source: 'finops-agent',
  },
  {
    findingId: 'f-1003',
    type: 'optimization',
    title: 'Unattached EBS volumes',
    description: '8 unattached gp3 EBS volumes totaling 640 GB have not been attached to an instance in 45+ days.',
    service: 'AmazonEC2',
    category: 'optimization',
    estimatedSavingsUsd: 64,
    status: 'accepted',
    priority: 'low',
    assignedTeamId: 't-platform',
    accountIds: ['123456789012'],
    createdAt: '2026-08-20T11:00:00Z',
    acceptedAt: '2026-08-21T10:15:00Z',
    acceptedBy: 'mock-admin-001',
    acceptNotes: 'Confirmed with team, safe to delete.',
    source: 'finops-agent',
  },
  {
    findingId: 'f-1004',
    type: 'optimization',
    title: 'S3 lifecycle policy missing on logs bucket',
    description: 'The application-logs bucket has 4.2 TB of objects older than 90 days with no lifecycle transition to Glacier configured.',
    service: 'AmazonS3',
    category: 'optimization',
    estimatedSavingsUsd: 310,
    status: 'rejected',
    priority: 'medium',
    assignedTeamId: 't-data',
    createdAt: '2026-08-15T08:00:00Z',
    rejectedAt: '2026-08-16T09:00:00Z',
    rejectedBy: 'mock-admin-001',
    rejectionReason: 'Logs are retained for compliance and accessed occasionally; Glacier retrieval latency is not acceptable.',
    rejectionCategory: 'compliance_requirement',
    source: 'finops-agent',
  },
  {
    findingId: 'f-1005',
    type: 'top-spender',
    title: 'RDS reserved instance coverage gap',
    description: 'On-demand RDS spend for db.r6g.xlarge instances in prod is $4,800/month with 0% reserved coverage.',
    service: 'AmazonRDS',
    category: 'optimization',
    estimatedSavingsUsd: 1650,
    status: 'pending',
    priority: 'high',
    assignedTeamId: 't-data',
    accountIds: ['123456789012'],
    tags: ['env:prod'],
    createdAt: '2026-08-29T07:45:00Z',
    source: 'finops-agent',
  },
];

const mockTeams: Team[] = [
  {
    teamId: 't-platform',
    name: 'Platform',
    description: 'Core infrastructure and shared services',
    members: ['mock-admin-001', 'user-002'],
    slackChannel: '#platform-finops',
    costCenter: 'CC-100',
    createdAt: '2026-01-10T00:00:00Z',
    createdBy: 'mock-admin-001',
    updatedAt: '2026-01-10T00:00:00Z',
  },
  {
    teamId: 't-data',
    name: 'Data',
    description: 'Data platform and analytics',
    members: ['user-003'],
    slackChannel: '#data-finops',
    costCenter: 'CC-200',
    createdAt: '2026-01-10T00:00:00Z',
    createdBy: 'mock-admin-001',
    updatedAt: '2026-01-10T00:00:00Z',
  },
  {
    teamId: 't-application',
    name: 'Application',
    description: 'Customer-facing application services',
    members: ['user-004', 'user-005'],
    slackChannel: '#app-finops',
    costCenter: 'CC-300',
    createdAt: '2026-01-10T00:00:00Z',
    createdBy: 'mock-admin-001',
    updatedAt: '2026-01-10T00:00:00Z',
  },
];

const mockScopingRules: ScopingRule[] = [
  { ruleId: 'r-1', type: 'serviceName', pattern: 'AmazonEC2', teamId: 't-platform', priority: 10, createdAt: '2026-01-10T00:00:00Z', createdBy: 'mock-admin-001' },
  { ruleId: 'r-2', type: 'serviceName', pattern: 'AmazonRDS', teamId: 't-data', priority: 10, createdAt: '2026-01-10T00:00:00Z', createdBy: 'mock-admin-001' },
  { ruleId: 'r-3', type: 'resourceTag', pattern: 'service:orders', teamId: 't-application', priority: 5, createdAt: '2026-01-10T00:00:00Z', createdBy: 'mock-admin-001' },
];

const mockLeaderboard: LeaderboardEntry[] = [
  { userId: 'user-004', userName: 'Jordan Lee', month: '2026-08', totalPoints: 480, totalSavingsUsd: 12400, findingsAccepted: 9, findingsRejected: 1, rank: 1 },
  { userId: 'mock-admin-001', userName: 'Demo Admin', month: '2026-08', totalPoints: 410, totalSavingsUsd: 8900, findingsAccepted: 7, findingsRejected: 2, rank: 2 },
  { userId: 'user-003', userName: 'Priya Nair', month: '2026-08', totalPoints: 355, totalSavingsUsd: 6200, findingsAccepted: 6, findingsRejected: 0, rank: 3 },
  { userId: 'user-002', userName: 'Sam Torres', month: '2026-08', totalPoints: 210, totalSavingsUsd: 3100, findingsAccepted: 4, findingsRejected: 1, rank: 4 },
];

const mockLearnings: Learning[] = [
  {
    learningId: 'l-1',
    findingId: 'f-1004',
    type: 'rejection',
    service: 'AmazonS3',
    category: 'optimization',
    title: 'S3 lifecycle policy missing on logs bucket',
    rejectionReason: 'Logs are retained for compliance and accessed occasionally; Glacier retrieval latency is not acceptable.',
    detailedFeedback: 'Compliance requires 1-year retention with retrieval SLA under 24 hours. Glacier Deep Archive would violate SLA.',
    suggestedImprovement: 'Consider S3 Intelligent-Tiering instead of Glacier for future recommendations on this bucket.',
    createdAt: '2026-08-16T09:00:00Z',
    createdBy: 'mock-admin-001',
  },
];

const mockDashboardStats: DashboardStats = {
  month: '2026-08',
  findings: { pending: 3, accepted: 1, rejected: 1, total: 5 },
  savings: { totalUsd: 5064, totalPoints: 890 },
  teams: { count: 3 },
  user: { points: 410, savingsUsd: 8900, findingsAccepted: 7, findingsRejected: 2 },
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

/**
 * Minimal in-memory mock router mirroring the real API's routes closely
 * enough to drive every page in the console.
 */
export async function mockRequest<T>(path: string, method: string, body?: unknown): Promise<T> {
  // Simulate latency so loading states are visible.
  await new Promise((resolve) => setTimeout(resolve, 200));

  const [rawPath, queryString] = path.split('?');
  const query = new URLSearchParams(queryString || '');

  if (rawPath === '/dashboard') {
    return clone(mockDashboardStats) as T;
  }

  if (rawPath === '/findings') {
    let results = clone(mockFindings);
    const status = query.get('status');
    const teamId = query.get('teamId');
    if (status) results = results.filter((f) => f.status === status);
    if (teamId) results = results.filter((f) => f.assignedTeamId === teamId);
    return { findings: results, count: results.length } as T;
  }

  const findingMatch = rawPath.match(/^\/findings\/([^/]+)(\/(accept|reject))?$/);
  if (findingMatch) {
    const findingId = findingMatch[1];
    const action = findingMatch[3];
    const finding = mockFindings.find((f) => f.findingId === findingId);

    if (action === 'accept' && finding) {
      finding.status = 'accepted';
      finding.acceptedAt = new Date().toISOString();
      finding.acceptedBy = 'mock-admin-001';
      return { message: 'Finding accepted (mock)', pointsEarned: 50, savingsUsd: finding.estimatedSavingsUsd } as T;
    }

    if (action === 'reject' && finding) {
      finding.status = 'rejected';
      finding.rejectedAt = new Date().toISOString();
      finding.rejectedBy = 'mock-admin-001';
      const data = body as { reason?: string } | undefined;
      finding.rejectionReason = data?.reason || 'Rejected in local preview';
      return { message: 'Finding rejected (mock)', pointsEarned: 10, learningRecorded: true } as T;
    }

    if (!action && method === 'GET' && finding) {
      return clone(finding) as T;
    }

    if (!action && method === 'PATCH' && finding) {
      Object.assign(finding, body);
      return clone(finding) as T;
    }
  }

  if (rawPath === '/teams') {
    if (method === 'POST') {
      const data = body as Partial<Team>;
      const newTeam: Team = {
        teamId: `t-${Date.now()}`,
        name: data.name || 'New Team',
        description: data.description,
        members: data.members || [],
        slackChannel: data.slackChannel,
        costCenter: data.costCenter,
        createdAt: new Date().toISOString(),
        createdBy: 'mock-admin-001',
        updatedAt: new Date().toISOString(),
      };
      mockTeams.push(newTeam);
      return clone(newTeam) as T;
    }
    return { teams: clone(mockTeams), count: mockTeams.length } as T;
  }

  if (rawPath === '/scoping') {
    if (method === 'POST') {
      const data = body as Partial<ScopingRule>;
      const newRule: ScopingRule = {
        ruleId: `r-${Date.now()}`,
        type: (data.type as ScopingRule['type']) || 'serviceName',
        pattern: data.pattern || '',
        teamId: data.teamId || '',
        description: data.description,
        priority: data.priority ?? 10,
        createdAt: new Date().toISOString(),
        createdBy: 'mock-admin-001',
      };
      mockScopingRules.push(newRule);
      return clone(newRule) as T;
    }
    return { rules: clone(mockScopingRules), count: mockScopingRules.length } as T;
  }

  if (rawPath === '/leaderboard') {
    return { month: '2026-08', leaderboard: clone(mockLeaderboard), count: mockLeaderboard.length } as T;
  }

  if (rawPath === '/learnings') {
    return { learnings: clone(mockLearnings), count: mockLearnings.length } as T;
  }

  const scoresMatch = rawPath.match(/^\/scores\/([^/]+)$/);
  if (scoresMatch) {
    const entry = mockLeaderboard.find((e) => e.userId === scoresMatch[1]);
    return clone(entry || mockLeaderboard[0]) as T;
  }

  throw new Error(`Mock API: no handler for ${method} ${path}`);
}
