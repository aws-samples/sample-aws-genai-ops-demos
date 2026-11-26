import type { StatusIndicatorProps } from '@cloudscape-design/components/status-indicator';
import type { BucketRow, ReportAi, Severity } from './types';

export interface StatusView {
  type: StatusIndicatorProps.Type;
  label: string;
}

// Same texts as s3dp/model.py (AI_NOTICE, RULES_NOTICE).
export const AI_NOTICE =
  'Text written by a generative AI assistant and estimates based on list prices: verify before acting.';
export const RULES_NOTICE =
  'Rules-based report: no generative AI was used. Estimates are based on list prices: verify before acting.';

const UNCONFIRMED: StatusView = { type: 'pending', label: 'Unconfirmed' };

const PROTECTION: Record<string, StatusView> = {
  protected: { type: 'success', label: 'Protected' },
  protected_governance: {
    type: 'success',
    label: 'Protected: Object Lock governance mode (can be bypassed)',
  },
  protected_lock: { type: 'success', label: 'Protected: Object Lock per-object retention' },
  likely: { type: 'info', label: 'Likely protected: to confirm' },
  partial: { type: 'warning', label: 'Partial' },
  unprotected: { type: 'error', label: 'Unprotected' },
  unknown: UNCONFIRMED,
};

const CELL: Record<string, StatusView> = {
  ok: { type: 'success', label: 'In place' },
  missing: { type: 'error', label: 'Missing' },
  partial: { type: 'warning', label: 'Partial' },
  na: { type: 'stopped', label: 'Not applicable' },
  unknown: UNCONFIRMED,
};

const COLOR: Record<string, StatusIndicatorProps.Type> = {
  red: 'error',
  orange: 'warning',
  yellow: 'warning',
  green: 'success',
};

const has = (o: object, k: unknown): k is string => typeof k === 'string' && Object.prototype.hasOwnProperty.call(o, k);

/** Value of he / ma / rd. Anything unrecognised is Unconfirmed, never "None" or "Off". */
export function protectionStatus(state: string | null | undefined): StatusView {
  return has(PROTECTION, state) ? PROTECTION[state] : UNCONFIRMED;
}

/** State of one cell of the control matrix. */
export function cellStatus(state: string | null | undefined): StatusView {
  return has(CELL, state) ? CELL[state] : UNCONFIRMED;
}

/** Overall bucket status: the colour picks the indicator, row.status is the text. */
export function bucketStatus(row: Pick<BucketRow, 'status' | 'status_color'>): StatusView {
  return { type: COLOR[row.status_color] ?? 'pending', label: row.status };
}

export function severityType(sev: Severity | string): StatusIndicatorProps.Type {
  return sev === 'HIGH' ? 'error' : sev === 'MEDIUM' ? 'warning' : 'info';
}

/** True when the generative AI assistant classified buckets or wrote report text (mirrors ai_used in model.py). */
export function aiUsed(meta: { ai?: ReportAi | null } | null | undefined): boolean {
  const ai = meta?.ai;
  if (!ai) return false;
  return Boolean(ai.classification) || ['pending', 'complete', 'partial'].includes(ai.narrative);
}

export function reportNotice(meta: { ai?: ReportAi | null } | null | undefined): string {
  return aiUsed(meta) ? AI_NOTICE : RULES_NOTICE;
}

/** Display text for a value that may be unknown: unknown-like values read "Unconfirmed", never blank or "None". */
export function unconfirmedText(v: string | null | undefined): string {
  if (v === null || v === undefined) return 'Unconfirmed';
  const t = String(v).trim();
  if (!t || /^(unknown|not[ _-]?verified)$/i.test(t)) return 'Unconfirmed';
  return t;
}
