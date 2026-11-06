export interface Finding {
  findingId: string;
  type: string;
  title: string;
  description: string;
  service: string;
  category: string;
  estimatedSavingsUsd: number;
  status: 'pending' | 'accepted' | 'rejected';
  priority: 'critical' | 'high' | 'medium' | 'low';
  assignedTeamId?: string;
  accountIds?: string[];
  resourceIds?: string[];
  tags?: string[];
  notes?: string;
  createdAt: string;
  updatedAt?: string;
  acceptedAt?: string;
  acceptedBy?: string;
  acceptNotes?: string;
  implementationDetails?: string;
  rejectedAt?: string;
  rejectedBy?: string;
  rejectionReason?: string;
  rejectionCategory?: string;
  relatedLearnings?: string[];
  source?: string;
}

export interface Team {
  teamId: string;
  name: string;
  description?: string;
  members: string[];
  slackChannel?: string;
  costCenter?: string;
  createdAt: string;
  createdBy: string;
  updatedAt: string;
}

export interface ScopingRule {
  ruleId: string;
  type: 'accountId' | 'resourceTag' | 'serviceName' | 'costCenter';
  pattern: string;
  teamId: string;
  description?: string;
  priority: number;
  createdAt: string;
  createdBy: string;
}

export interface LeaderboardEntry {
  userId: string;
  userName: string;
  month: string;
  totalPoints: number;
  totalSavingsUsd: number;
  findingsAccepted: number;
  findingsRejected: number;
  lastActivity?: string;
  rank?: number;
}

export interface DashboardStats {
  month: string;
  findings: {
    pending: number;
    accepted: number;
    rejected: number;
    total: number;
  };
  savings: {
    totalUsd: number;
    totalPoints: number;
  };
  teams: {
    count: number;
  };
  user: {
    points: number;
    savingsUsd: number;
    findingsAccepted: number;
    findingsRejected: number;
  };
}

export interface Learning {
  learningId: string;
  findingId: string;
  type: 'acceptance' | 'rejection';
  service: string;
  category: string;
  title: string;
  description?: string;
  rejectionReason?: string;
  detailedFeedback?: string;
  suggestedImprovement?: string;
  implementationDetails?: string;
  estimatedSavingsUsd?: number;
  tags?: string[];
  createdAt: string;
  createdBy: string;
}

export const REJECTION_CATEGORIES = [
  { value: 'already_implemented', label: 'Already Implemented' },
  { value: 'business_constraint', label: 'Business Constraint' },
  { value: 'technical_blocker', label: 'Technical Blocker' },
  { value: 'incorrect_analysis', label: 'Incorrect Analysis' },
  { value: 'missing_context', label: 'Missing Context' },
  { value: 'compliance_requirement', label: 'Compliance Requirement' },
  { value: 'performance_impact', label: 'Performance Impact' },
  { value: 'other', label: 'Other' },
] as const;

export const PRIORITY_COLORS: Record<Finding['priority'], string> = {
  critical: 'red',
  high: 'orange',
  medium: 'blue',
  low: 'grey',
};

export const STATUS_COLORS: Record<Finding['status'], string> = {
  pending: 'blue',
  accepted: 'green',
  rejected: 'grey',
};
