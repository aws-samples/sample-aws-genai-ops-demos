import { isExcluded } from './exclude';
import type { BucketRow, RiskKey } from './types';

export interface FilterState {
  text: string;
  account: string;
  region: string;
  role: string;
  status: string;
  tier: string;
  spotlightOnly: boolean;
  patterns: string[];
}

export const emptyFilter = (): FilterState => ({
  text: '', account: '', region: '', role: '', status: '', tier: '', spotlightOnly: false, patterns: [],
});

/** Risk keys of a row to the names used in `row.req`. */
export const RISK_REQ: Record<RiskKey, string> = {
  he: 'human_error',
  ma: 'malicious_activity',
  rd: 'regional_disruption',
};
const RISKS = Object.keys(RISK_REQ) as RiskKey[];

const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB'];

export function formatBytes(n: number): string {
  let v = Number.isFinite(n) && n > 0 ? n : 0;
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i++;
  }
  if (i === 0) return `${Math.round(v)} B`;
  return `${v >= 10 ? Math.round(v) : v.toFixed(1)} ${UNITS[i]}`;
}

export function filterRows(rows: BucketRow[], s: FilterState): BucketRow[] {
  const text = s.text.trim().toLowerCase();
  return rows.filter((r) => {
    if (isExcluded(r.bucket ?? '', s.patterns)) return false;
    if (s.account && r.account !== s.account) return false;
    if (s.region && r.region !== s.region) return false;
    if (s.role && r.role !== s.role) return false;
    if (s.status && r.status !== s.status) return false;
    if (s.tier && r.tier !== s.tier) return false;
    if (s.spotlightOnly && !r.spotlight) return false;
    if (text) {
      const hay = [r.bucket, r.account, r.region, r.role, r.action, ...(r.reasons ?? [])].join('\n').toLowerCase();
      if (!hay.includes(text)) return false;
    }
    return true;
  });
}

export interface Summary {
  buckets: number;
  totalBytes: number;
  spotlight: number;
  unprotectedRequired: Record<RiskKey, number>;
  /** Required risks whose state is unknown or unrecognised: never counted as unprotected. */
  unconfirmedRequired: Record<RiskKey, number>;
}

const KNOWN = ['protected', 'protected_governance', 'protected_lock', 'likely', 'partial', 'unprotected'];

export function summary(rows: BucketRow[]): Summary {
  const zero = () => ({ he: 0, ma: 0, rd: 0 });
  const out: Summary = { buckets: rows.length, totalBytes: 0, spotlight: 0, unprotectedRequired: zero(), unconfirmedRequired: zero() };
  for (const r of rows) {
    out.totalBytes += r.size_bytes || 0;
    if (r.spotlight) out.spotlight++;
    for (const k of RISKS) {
      if (!(r.req ?? []).includes(RISK_REQ[k])) continue;
      if (r[k] === 'unprotected') out.unprotectedRequired[k]++;
      else if (!KNOWN.includes(r[k])) out.unconfirmedRequired[k]++;
    }
  }
  return out;
}

export function options(rows: BucketRow[], key: 'account' | 'region' | 'role' | 'status' | 'tier'): string[] {
  return [...new Set(rows.map((r) => r[key]).filter(Boolean))].sort();
}

/** Shortens a long name in the middle, keeping the start and the distinguishing suffix. */
export function middleTruncate(name: string, head = 10, tail = 18): string {
  if (name.length <= head + tail + 1) return name;
  return `${name.slice(0, head)}\u2026${name.slice(name.length - tail)}`;
}
