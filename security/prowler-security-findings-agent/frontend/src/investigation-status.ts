import type { AgentJournalRecord, InvestigationState, LiveSummary } from './api';

export type RunState = 'running' | 'failed' | 'done';

const RUNNING_TASK_STATUSES = new Set(['IN_PROGRESS', 'RUNNING', 'ACTIVE', 'WAITING']);
const FAILED_TASK_STATUSES = new Set(['FAILED', 'ERROR', 'TIMED_OUT', 'CANCELED', 'CANCELLED', 'SKIPPED']);

/** For a raw backlog task status: queued and waiting states (PENDING_*) count as running. */
export function taskState(status?: string): RunState {
  const s = (status || '').toUpperCase();
  if (RUNNING_TASK_STATUSES.has(s) || s.startsWith('PENDING')) return 'running';
  if (FAILED_TASK_STATUSES.has(s)) return 'failed';
  return 'done';
}

export function isTaskRunning(status?: string): boolean {
  return taskState(status) === 'running';
}

/** Status of GET /findings/{uid}/investigation. Older APIs report 'pending' when every task failed. */
export function investigationState(inv: InvestigationState | null): RunState {
  if (!inv) return 'done';
  if (inv.status === 'failed') return 'failed';
  if (inv.status === 'in_progress') return 'running';
  if (inv.status !== 'pending') return 'done';
  const allFailed = inv.tasks.length > 0 && inv.tasks.every((t) => taskState(t.status) === 'failed');
  return allFailed ? 'failed' : 'running';
}

export function isInvestigationRunning(inv: InvestigationState | null): boolean {
  return investigationState(inv) === 'running';
}

/** The API sends null until there is a summary; an empty one is treated the same. */
export function hasSummary(s: LiveSummary | null | undefined): s is LiveSummary {
  return !!s && typeof s.content === 'string' && s.content.trim() !== '';
}

// Context-window telemetry and the summary records, which the Live summary panel shows.
const HIDDEN_JOURNAL_TYPES = new Set([
  'utilization',
  'ui_investigation_summary',
  'investigation_summary',
  'investigation_summary_md',
  'mitigation_summary',
  'mitigation_summary_md',
]);

export function isShownJournalRecord(r: AgentJournalRecord): boolean {
  return !HIDDEN_JOURNAL_TYPES.has((r.type || '').toLowerCase());
}
