import type { Run } from '../api';
import type { StatusView } from './status';

export function runStatus(run: Pick<Run, 'status'>): StatusView {
  switch (run.status) {
    case 'RUNNING':
      return { type: 'in-progress', label: 'Running' };
    case 'SUCCEEDED':
      return { type: 'success', label: 'Completed' };
    case 'FAILED':
      return { type: 'error', label: 'Failed' };
    default:
      return { type: 'pending', label: 'Unconfirmed' };
  }
}

export function recsLabel(recs: Run['recs']): string {
  return recs ? `${recs.HIGH} / ${recs.MEDIUM} / ${recs.LOW}` : '-';
}

export function isActive(run: Pick<Run, 'status'> | null | undefined): boolean {
  return run?.status === 'RUNNING';
}

/** Status of one account's outcome text ("scanned", "partial: ...", "not scanned: ...", "denied: ...", "scanning"). */
export function accountOutcome(outcome: string): StatusView['type'] {
  if (outcome === 'scanned') return 'success';
  if (outcome === 'scanning') return 'in-progress';
  if (outcome.startsWith('partial')) return 'warning';
  if (outcome.startsWith('not scanned') || outcome.startsWith('denied')) return 'error';
  return 'pending';
}

export function stepStatus(s: string): 'pending' | 'in-progress' | 'success' | 'error' {
  return s === 'in-progress' || s === 'success' || s === 'error' ? s : 'pending';
}
