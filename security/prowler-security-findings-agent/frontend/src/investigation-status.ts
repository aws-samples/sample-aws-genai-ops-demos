import type { InvestigationState, LiveSummary } from './api';

const RUNNING_TASK_STATUSES = new Set(['PENDING', 'IN_PROGRESS', 'RUNNING', 'ACTIVE']);
const FAILED_TASK_STATUSES = new Set(['FAILED', 'ERROR', 'CANCELLED', 'CANCELED', 'TIMED_OUT']);

/** For the raw backlog task status in GET /investigations. */
export function isTaskRunning(status?: string): boolean {
  return RUNNING_TASK_STATUSES.has((status || '').toUpperCase());
}

/** GET /findings/{uid}/investigation reports 'pending' when every task failed. */
export function isInvestigationRunning(inv: InvestigationState | null): boolean {
  if (!inv) return false;
  if (inv.status === 'in_progress') return true;
  if (inv.status !== 'pending') return false;
  return !(inv.tasks.length > 0 && inv.tasks.every((t) => FAILED_TASK_STATUSES.has((t.status || '').toUpperCase())));
}

/** The API sends null until there is a summary; an empty one is treated the same. */
export function hasSummary(s: LiveSummary | null | undefined): s is LiveSummary {
  return !!s && typeof s.content === 'string' && s.content.trim() !== '';
}
