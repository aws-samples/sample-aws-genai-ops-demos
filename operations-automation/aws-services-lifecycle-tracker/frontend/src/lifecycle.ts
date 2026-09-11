// Shared lifecycle vocabulary for the UI (issue #141).
//
// One place that decides how a status is labelled and coloured, how an
// inventory row is recognised, and how "when does this bite me" is computed,
// so every page tells the same story.
import type { DeprecationItem, LifecycleStatus } from './api';

export type IndicatorType = 'success' | 'warning' | 'error' | 'info' | 'pending' | 'stopped';

export interface StatusMeta {
  label: string;       // human wording shown in badges
  short: string;       // KPI / compact wording
  indicator: IndicatorType;
  // Ordering for "urgency first" sorts: lower = more urgent
  rank: number;
  // True when the row represents an actual lifecycle concern
  concern: boolean;
}

// Internal status -> what a viewer should read.
//   end_of_life          the end date passed: the version is gone / blocked
//   deprecated           deprecation effective now (still runs, no more support)
//   extended_support     past standard support (fees / final stretch)
//   end_of_support_date  end of support announced, less than a year away
//   supported            nothing to do
//   unknown              inventory row that matched no fact
export const STATUS_META: Record<LifecycleStatus, StatusMeta> = {
  end_of_life:         { label: 'End of life',           short: 'End of life',      indicator: 'error',   rank: 0, concern: true },
  deprecated:          { label: 'Deprecated',            short: 'Deprecated',       indicator: 'error',   rank: 1, concern: true },
  extended_support:    { label: 'Past standard support', short: 'Past std support', indicator: 'warning', rank: 2, concern: true },
  end_of_support_date: { label: 'Ending within a year',  short: 'Ending < 1 year',  indicator: 'warning', rank: 3, concern: true },
  supported:           { label: 'Supported',             short: 'Supported',        indicator: 'success', rank: 5, concern: false },
  unknown:             { label: 'Not matched',           short: 'Not matched',      indicator: 'pending', rank: 4, concern: false },
};

export const statusMeta = (status: string): StatusMeta =>
  STATUS_META[status as LifecycleStatus] || { label: status, short: status, indicator: 'pending', rank: 6, concern: false };

export const isConcern = (status: string): boolean => statusMeta(status).concern;

// Inventory rows (what was found in the account) vs facts (what AWS publishes)
export const isInventory = (item: DeprecationItem): boolean => item.item_id.startsWith('inventory#');

// The date that matters for "when does this bite me": the earliest future
// lifecycle date, or the most recent past one when everything has passed.
const DEADLINE_FIELDS = [
  'end_of_support_date',
  'end_of_standard_support_date',
  'deprecation_date',
  'retirement_date',
  'target_retirement_date',
  'end_of_life_date',
  'eol_date',
  'block_update_date',
  'block_create_date',
  'end_of_extended_support_date',
];

const parseDate = (value: unknown): Date | null => {
  if (!value || typeof value !== 'string') return null;
  const s = value.trim();
  if (!s || /^(n\/a|none|null|tbd|-|--)$/i.test(s)) return null;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
};

export interface Deadline {
  date: Date;
  field: string;
  daysLeft: number; // negative = already passed
}

export const getDeadline = (item: DeprecationItem, now: Date = new Date()): Deadline | null => {
  const dates: Deadline[] = [];
  for (const field of DEADLINE_FIELDS) {
    const d = parseDate(item.service_specific?.[field]);
    if (d) dates.push({ date: d, field, daysLeft: Math.ceil((d.getTime() - now.getTime()) / 86_400_000) });
  }
  if (dates.length === 0) return null;
  const future = dates.filter((d) => d.daysLeft >= 0).sort((a, b) => a.daysLeft - b.daysLeft);
  if (future.length) return future[0];
  return dates.sort((a, b) => b.daysLeft - a.daysLeft)[0];
};

export type Urgency = 'past' | 'soon' | 'year' | 'later' | 'none';

export const urgencyOf = (deadline: Deadline | null): Urgency => {
  if (!deadline) return 'none';
  if (deadline.daysLeft < 0) return 'past';
  if (deadline.daysLeft <= 90) return 'soon';
  if (deadline.daysLeft <= 365) return 'year';
  return 'later';
};

export const URGENCY_META: Record<Urgency, { label: string; indicator: IndicatorType }> = {
  past: { label: 'Passed', indicator: 'error' },
  soon: { label: '≤ 90 days', indicator: 'error' },
  year: { label: '≤ 1 year', indicator: 'warning' },
  later: { label: '> 1 year', indicator: 'success' },
  none: { label: 'No date', indicator: 'pending' },
};

export const formatDaysLeft = (deadline: Deadline | null): string => {
  if (!deadline) return '-';
  const d = deadline.daysLeft;
  if (d < 0) return `${Math.abs(d)} days ago`;
  if (d === 0) return 'today';
  if (d < 60) return `in ${d} days`;
  return `in ${Math.round(d / 30)} months`;
};

export const formatDate = (value: unknown): string => {
  const d = parseDate(value);
  return d ? d.toISOString().slice(0, 10) : '-';
};

// Sort key: concern first, then closest deadline, then status rank
export const urgencySort = (a: DeprecationItem, b: DeprecationItem): number => {
  const ca = isConcern(a.status) ? 0 : 1;
  const cb = isConcern(b.status) ? 0 : 1;
  if (ca !== cb) return ca - cb;
  const da = getDeadline(a)?.daysLeft ?? Number.MAX_SAFE_INTEGER;
  const db = getDeadline(b)?.daysLeft ?? Number.MAX_SAFE_INTEGER;
  if (da !== db) return da - db;
  return statusMeta(a.status).rank - statusMeta(b.status).rank;
};

// Human name for a config service key (falls back to the key)
export const SERVICE_LABELS: Record<string, string> = {
  lambda: 'Lambda', eks: 'EKS', rds: 'RDS', aurora: 'Aurora', elasticache: 'ElastiCache',
  opensearch: 'OpenSearch', elasticbeanstalk: 'Elastic Beanstalk', msk: 'MSK', neptune: 'Neptune',
  glue: 'Glue', documentdb: 'DocumentDB', ec2: 'EC2',
};
export const serviceLabel = (key: string): string => SERVICE_LABELS[key] || key;

// Display name of a row: inventory rows already carry a display name
export const itemName = (item: DeprecationItem): string =>
  item.service_specific?.name || item.service_specific?.identifier || item.item_id.split('#').pop() || item.item_id;

// Count of my resources matching a given fact (by matched_lifecycle_item)
export const resourcesByFact = (inventory: DeprecationItem[]): Map<string, DeprecationItem[]> => {
  const m = new Map<string, DeprecationItem[]>();
  for (const row of inventory) {
    const key = `${row.service_name}|${row.service_specific?.matched_lifecycle_item || ''}`;
    if (!row.service_specific?.matched_lifecycle_item) continue;
    m.set(key, [...(m.get(key) || []), row]);
  }
  return m;
};

// Number of resources behind an inventory row (exact, even when the stored
// name list is capped).
export const resourceCount = (row: DeprecationItem): number =>
  Number(row.service_specific?.total_affected) || 0;

export const resourceWord = (n: number): string => (n === 1 ? 'resource' : 'resources');

export interface ResourceRef {
  name: string;
  arn?: string;
  console_url?: string;
}

// Resources behind an inventory row, richest source first: per-resource
// details (name, ARN, console deep link; stored since #141, capped
// server-side), then the plain name list, then the legacy summary string
// written by older scans.
export const resourceDetails = (row: DeprecationItem): ResourceRef[] => {
  const details = row.service_specific?.affected_resource_details;
  if (Array.isArray(details) && details.length) {
    return details.map((d: any) => ({ name: String(d.name ?? ''), arn: d.arn || undefined, console_url: d.console_url || undefined }));
  }
  const list = row.service_specific?.affected_resource_names;
  if (Array.isArray(list) && list.length) return list.map((n: unknown) => ({ name: String(n) }));
  const legacy = String(row.service_specific?.affected_resources || '');
  return legacy
    .replace(/\s*\(\+\d+ more\)\s*$/, '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((name) => ({ name }));
};

export const resourceNames = (row: DeprecationItem): string[] => resourceDetails(row).map((r) => r.name);
