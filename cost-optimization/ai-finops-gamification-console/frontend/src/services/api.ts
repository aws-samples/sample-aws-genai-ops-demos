import { fetchAuthSession } from 'aws-amplify/auth';
import { Finding, Team, ScopingRule, LeaderboardEntry, DashboardStats, Learning } from '../types';

const API_ENDPOINT = import.meta.env.VITE_API_ENDPOINT || '';

async function getAuthHeaders(): Promise<HeadersInit> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString() || '';
  return {
    'Content-Type': 'application/json',
    'Authorization': token,
  };
}

async function apiRequest<T>(
  path: string,
  method: string = 'GET',
  body?: unknown
): Promise<T> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_ENDPOINT}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Request failed' }));
    throw new Error(error.error || `HTTP ${response.status}`);
  }

  return response.json();
}

// Dashboard
export async function getDashboardStats(): Promise<DashboardStats> {
  return apiRequest<DashboardStats>('/dashboard');
}

// Findings
export async function getFindings(params?: { status?: string; teamId?: string }): Promise<{ findings: Finding[]; count: number }> {
  const queryParams = new URLSearchParams();
  if (params?.status) queryParams.set('status', params.status);
  if (params?.teamId) queryParams.set('teamId', params.teamId);
  const query = queryParams.toString();
  return apiRequest<{ findings: Finding[]; count: number }>(`/findings${query ? `?${query}` : ''}`);
}

export async function getFinding(findingId: string): Promise<Finding> {
  return apiRequest<Finding>(`/findings/${findingId}`);
}

export async function updateFinding(findingId: string, data: Partial<Finding>): Promise<Finding> {
  return apiRequest<Finding>(`/findings/${findingId}`, 'PATCH', data);
}

export async function acceptFinding(
  findingId: string,
  data: { notes?: string; implementationDetails?: string; createLearning?: boolean }
): Promise<{ message: string; pointsEarned: number; savingsUsd: number }> {
  return apiRequest(`/findings/${findingId}/accept`, 'POST', data);
}

export async function rejectFinding(
  findingId: string,
  data: { reason: string; category: string; detailedFeedback?: string; suggestedImprovement?: string }
): Promise<{ message: string; pointsEarned: number; learningRecorded: boolean }> {
  return apiRequest(`/findings/${findingId}/reject`, 'POST', data);
}

// Teams
export async function getTeams(): Promise<{ teams: Team[]; count: number }> {
  return apiRequest<{ teams: Team[]; count: number }>('/teams');
}

export async function getTeam(teamId: string): Promise<Team> {
  return apiRequest<Team>(`/teams/${teamId}`);
}

export async function createTeam(data: Omit<Team, 'teamId' | 'createdAt' | 'createdBy' | 'updatedAt'>): Promise<Team> {
  return apiRequest<Team>('/teams', 'POST', data);
}

export async function updateTeam(teamId: string, data: Partial<Team>): Promise<Team> {
  return apiRequest<Team>(`/teams/${teamId}`, 'PUT', data);
}

export async function deleteTeam(teamId: string): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/teams/${teamId}`, 'DELETE');
}

// Scoping Rules
export async function getScopingRules(): Promise<{ rules: ScopingRule[]; count: number }> {
  return apiRequest<{ rules: ScopingRule[]; count: number }>('/scoping');
}

export async function createScopingRule(data: {
  type: string;
  pattern: string;
  teamId: string;
  description?: string;
  priority?: number;
}): Promise<ScopingRule> {
  return apiRequest<ScopingRule>('/scoping', 'POST', data);
}

export async function deleteScopingRule(ruleId: string): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(`/scoping/${ruleId}`, 'DELETE');
}

// Leaderboard
export async function getLeaderboard(month?: string): Promise<{ month: string; leaderboard: LeaderboardEntry[]; count: number }> {
  const query = month ? `?month=${month}` : '';
  return apiRequest(`/leaderboard${query}`);
}

export async function getUserScores(userId: string, month?: string): Promise<LeaderboardEntry | { scores: LeaderboardEntry[] }> {
  const query = month ? `?month=${month}` : '';
  return apiRequest(`/scores/${userId}${query}`);
}

// Learnings
export async function getLearnings(service?: string): Promise<{ learnings: Learning[]; count: number }> {
  const query = service ? `?service=${service}` : '';
  return apiRequest(`/learnings${query}`);
}
