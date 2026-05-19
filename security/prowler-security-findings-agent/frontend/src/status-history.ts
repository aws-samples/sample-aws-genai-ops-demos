import { Finding } from './api';

export type HistoryBadge =
  | { kind: 'new'; label: string }
  | { kind: 'fixed'; label: string; since: string }
  | { kind: 'regressed'; label: string; wasStatus: string }
  | { kind: 'stable' };

/**
 * Look at a finding's status_history and derive a one-word badge that says
 * *what changed in the last scan*. "new" when this is the only entry,
 * "fixed" when the status flipped to PASS, "regressed" when it flipped
 * FAIL/MANUAL from a previous PASS, "stable" otherwise.
 *
 * The UI only highlights "new" / "fixed" / "regressed" — "stable" is the
 * default and gets no chip.
 */
export function badgeFromHistory(f: Finding): HistoryBadge {
  const h = f.status_history || [];
  if (h.length === 0) return { kind: 'stable' };
  if (h.length === 1) return { kind: 'new', label: 'New' };
  const last = h[h.length - 1];
  const prev = h[h.length - 2];
  if (last.status === prev.status) return { kind: 'stable' };
  if (last.status === 'PASS' && prev.status !== 'PASS') {
    return { kind: 'fixed', label: 'Fixed', since: last.scan_id };
  }
  if ((last.status === 'FAIL' || last.status === 'MANUAL') && prev.status === 'PASS') {
    return { kind: 'regressed', label: 'Regressed', wasStatus: prev.status };
  }
  return { kind: 'stable' };
}
