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

// --- Accounts (multi-account scan, #144) ------------------------------------

// Account of an inventory row; rows written before #144 have none.
export const accountId = (row: DeprecationItem): string => row.account_id || '';

// "Account A (222222222222)" when the name is known, else the id alone.
export const accountLabel = (id: string, name?: string): string => (name ? `${name} (${id})` : id || '-');

// Distinct accounts present in a set of rows, hub-independent, sorted by label.
export const accountsIn = (rows: DeprecationItem[]): Array<{ id: string; name: string }> => {
  const m = new Map<string, string>();
  for (const r of rows) if (r.account_id) m.set(r.account_id, r.account_name || m.get(r.account_id) || '');
  return [...m].map(([id, name]) => ({ id, name })).sort((a, b) => accountLabel(a.id, a.name).localeCompare(accountLabel(b.id, b.name)));
};

// AWS Health notice naming this resource (stamped at scan time, #141)
export interface HealthFlag {
  event_arn: string;
  event_type: string;      // e.g. AWS_LAMBDA_PLANNED_LIFECYCLE_EVENT
  event_status: string;    // open | upcoming
  entity_status: string;   // PENDING | RESOLVED | (empty)
  start_time?: string;
  end_time?: string;
  console_url?: string;
}

// RDS/Aurora Extended Support estimate for one resource (stamped at scan time, #142).
// Every input of the calculation is stored so the UI can show the arithmetic.
export interface ExtendedSupportEstimate {
  eligible: boolean;
  reason?: 'no_extended_support' | 'no_price' | 'unknown_instance_class' | 'no_capacity' | 'error';
  note?: string;
  currency: string;
  engine_family?: string;
  major_version?: string;
  instance_class?: string;
  serverless?: boolean;
  multi_az?: boolean;
  vcpus?: number;
  billable_vcpus?: number;
  min_acu?: number;
  max_acu?: number;
  unit?: 'vCPU-hour' | 'ACU-hour';
  price_yr1_2?: number;
  price_yr3?: number;
  price_source?: 'sku' | 'family-estimate';
  monthly_yr1_2?: number;
  monthly_yr1_2_min?: number;
  monthly_yr3?: number;
  forecast_12m?: number;
  standard_support_end?: string | null;
  extended_support_start?: string | null;
  year3_start?: string | null;
  extended_support_end?: string | null;
  in_extended_support?: boolean;
}

// Row-level aggregate of the estimates above
export interface CostExposure {
  currency: string;
  resources_priced: number;
  resources_total: number;
  monthly: number;
  monthly_yr3: number;
  forecast_12m: number;
  in_extended_support: number;
  estimated: number;
}

export const HOURS_PER_MONTH = 730; // always-on assumption, same as the backend

export interface ResourceRef {
  name: string;
  arn?: string;
  console_url?: string;
  health?: HealthFlag;
  extended_support?: ExtendedSupportEstimate;
  // RDS/Aurora: the exact minor running and the date RDS auto-upgrades it to a
  // newer minor. Distinct from the row deadline (the major's end of standard
  // support, which starts the Extended Support bill).
  minor_version?: string;
  minor_end_of_support?: string;
}

// When the Extended Support money of a row (or a set of rows) actually starts.
// Splits priced resources into billing now / starting within 12 months / later,
// so the UI never adds a 2031 bill to a 2027 one.
export interface CostTimeline {
  priced: number;
  now: number; monthlyNow: number;
  within12: number; monthlyWithin12: number;
  later: number; monthlyLater: number;
  forecast12: number;
  nextStart: string | null;   // earliest extended_support_start not yet reached
}

export const costTimeline = (rows: DeprecationItem[]): CostTimeline => {
  const t: CostTimeline = { priced: 0, now: 0, monthlyNow: 0, within12: 0, monthlyWithin12: 0, later: 0, monthlyLater: 0, forecast12: 0, nextStart: null };
  for (const row of rows) {
    for (const r of resourceDetails(row)) {
      const e = r.extended_support;
      if (!e?.eligible || e.monthly_yr1_2 === undefined) continue;
      t.priced += 1;
      t.forecast12 += e.forecast_12m ?? 0;
      if (e.in_extended_support) { t.now += 1; t.monthlyNow += e.monthly_yr1_2; continue; }
      if (e.extended_support_start && (!t.nextStart || e.extended_support_start < t.nextStart)) t.nextStart = e.extended_support_start;
      if ((e.forecast_12m ?? 0) > 0) { t.within12 += 1; t.monthlyWithin12 += e.monthly_yr1_2; }
      else { t.later += 1; t.monthlyLater += e.monthly_yr1_2; }
    }
  }
  return t;
};

// "Aug 2029" for an ISO date
export const formatMonth = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', year: 'numeric' }) : '';

export const costExposure = (row: DeprecationItem): CostExposure | null => {
  const c = row.service_specific?.cost_exposure;
  return c && typeof c === 'object' ? (c as CostExposure) : null;
};

export const formatUsd = (n: number | undefined | null, digits = 0): string =>
  n === undefined || n === null ? '-' : `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

// The calculation, spelled out: "2 vCPU × 2 (Multi-AZ) × $0.122/vCPU-h × 730 h"
export const estimateFormula = (e: ExtendedSupportEstimate): string => {
  if (!e.eligible || e.price_yr1_2 === undefined) return '';
  if (e.serverless) {
    return `${e.max_acu ?? 0} ACU (max) × $${e.price_yr1_2}/ACU-h × ${HOURS_PER_MONTH} h`;
  }
  const az = e.multi_az ? ' × 2 (Multi-AZ)' : '';
  return `${e.vcpus ?? '?'} vCPU${az} × $${e.price_yr1_2}/vCPU-h × ${HOURS_PER_MONTH} h`;
};

// Resources of a row that AWS Health names in an open notice (exact count
// stored by the scan; 0 when Health was unavailable).
export const healthFlagged = (row: DeprecationItem): number =>
  Number(row.service_specific?.health_flagged) || 0;

// Human wording for an AWS Health event type code
export const healthEventLabel = (code: string): string =>
  code.replace(/^AWS_/, '').replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

// Resources behind an inventory row, richest source first: per-resource
// details (name, ARN, console deep link; stored since #141, capped
// server-side), then the plain name list, then the legacy summary string
// written by older scans.
export const resourceDetails = (row: DeprecationItem): ResourceRef[] => {
  const details = row.service_specific?.affected_resource_details;
  if (Array.isArray(details) && details.length) {
    return details.map((d: any) => ({
      name: String(d.name ?? ''), arn: d.arn || undefined, console_url: d.console_url || undefined,
      health: d.health && d.health.event_arn ? (d.health as HealthFlag) : undefined,
      extended_support: d.extended_support && typeof d.extended_support === 'object' ? (d.extended_support as ExtendedSupportEstimate) : undefined,
      minor_version: d.minor_version || undefined,
      minor_end_of_support: d.minor_end_of_support || undefined,
    }));
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
