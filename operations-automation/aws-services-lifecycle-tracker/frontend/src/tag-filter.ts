// Tag filter (#164): scope the whole tracker to the resources carrying given
// user tags (organizations mark ownership with tags such as BU or Team).
//
// Model = the Resource Groups Tagging API's TagFilter: { key, values[] }. Several
// values of one key are OR, several keys are AND, a key without values means
// "tagged with this key". The active filter is applied once, in the data layer
// (getLifecycleData), so every page follows without knowing about it.
import type { DeprecationItem } from './api';

export interface TagFilter { key: string; values: string[] }

export const NOT_TAGGED = '(not tagged)';

// ---- serialization: URL (?tag=BU:LOB1&tag=BU:LOB2&tag=Team) and localStorage ----

export const STORAGE_KEY = 'lifecycle-tag-filter';

export const tokenText = (f: TagFilter): string => (f.values.length ? `${f.key}: ${f.values.join(' OR ')}` : f.key);

export const filtersToParams = (filters: TagFilter[]): string[] =>
  filters.flatMap((f) => (f.values.length ? f.values.map((v) => `${f.key}:${v}`) : [f.key]));

export const paramsToFilters = (params: string[]): TagFilter[] => {
  const out: TagFilter[] = [];
  for (const p of params) {
    const i = p.indexOf(':');
    const key = (i >= 0 ? p.slice(0, i) : p).trim();
    const value = i >= 0 ? p.slice(i + 1) : '';
    if (!key) continue;
    const f = out.find((x) => x.key === key);
    if (f) { if (value && !f.values.includes(value)) f.values.push(value); }
    else out.push({ key, values: value ? [value] : [] });
  }
  return out;
};

// Merge like the console: same key = one filter, values unioned
export const addFilter = (filters: TagFilter[], key: string, value: string): TagFilter[] => {
  const next = filters.map((f) => ({ ...f, values: [...f.values] }));
  const f = next.find((x) => x.key === key);
  if (f) { if (value && !f.values.includes(value)) f.values.push(value); if (!value) f.values = []; }
  else next.push({ key, values: value ? [value] : [] });
  return next;
};

export const loadStoredFilters = (): TagFilter[] => {
  try { return paramsToFilters(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')); } catch { return []; }
};
export const storeFilters = (filters: TagFilter[]) => {
  if (filters.length) localStorage.setItem(STORAGE_KEY, JSON.stringify(filtersToParams(filters)));
  else localStorage.removeItem(STORAGE_KEY);
};

// ---- matching ----

const detailsOf = (row: DeprecationItem): any[] => {
  const d = row.service_specific?.affected_resource_details;
  return Array.isArray(d) ? d : [];
};

export const matchesTags = (tags: Record<string, string> | undefined, filters: TagFilter[]): boolean =>
  filters.every((f) => {
    if (f.values.includes(NOT_TAGGED)) return !tags || !(f.key in tags);
    if (!tags || !(f.key in tags)) return false;
    return f.values.length === 0 || f.values.includes(tags[f.key]);
  });

// Row-level aggregate of the Extended Support estimates of the kept entries
// (same arithmetic as backend/cost_estimator.row_exposure)
const rowExposure = (details: any[], previous: any) => {
  const priced = details.map((d) => d.extended_support).filter((e) => e && e.eligible && e.monthly_yr1_2 !== undefined);
  const r2 = (n: number) => Math.round(n * 100) / 100;
  return {
    currency: previous?.currency || 'USD',
    resources_total: details.length,
    resources_priced: priced.length,
    monthly: r2(priced.reduce((n, e) => n + (e.monthly_yr1_2 || 0), 0)),
    monthly_yr3: r2(priced.reduce((n, e) => n + (e.monthly_yr3 || 0), 0)),
    forecast_12m: r2(priced.reduce((n, e) => n + (e.forecast_12m || 0), 0)),
    in_extended_support: priced.filter((e) => e.in_extended_support).length,
    estimated: priced.filter((e) => e.price_source === 'family-estimate').length,
  };
};

export interface ScopeStats { total: number; matched: number; rowsTotal: number; rowsMatched: number }

// Rows narrowed to the resources matching the filter: entries reduced,
// total_affected/health_flagged/cost_exposure recomputed, empty rows dropped.
// Counts are exact up to the stored entries (the scan caps them; see README).
export const scopeInventory = (rows: DeprecationItem[], filters: TagFilter[]): { rows: DeprecationItem[]; stats: ScopeStats } => {
  const stats: ScopeStats = { total: 0, matched: 0, rowsTotal: rows.length, rowsMatched: 0 };
  if (!filters.length) {
    stats.total = stats.matched = rows.reduce((n, r) => n + (Number(r.service_specific?.total_affected) || detailsOf(r).length), 0);
    stats.rowsMatched = rows.length;
    return { rows, stats };
  }
  const out: DeprecationItem[] = [];
  for (const row of rows) {
    const details = detailsOf(row);
    stats.total += Number(row.service_specific?.total_affected) || details.length;
    const kept = details.filter((d) => matchesTags(d.tags, filters));
    if (!kept.length) continue;
    stats.matched += kept.length;
    stats.rowsMatched += 1;
    const ss = row.service_specific || {};
    out.push({
      ...row,
      service_specific: {
        ...ss,
        affected_resource_details: kept,
        affected_resource_names: kept.map((d) => d.name),
        affected_resources: kept.slice(0, 3).map((d) => d.name).join(', ') + (kept.length > 3 ? ` (+${kept.length - 3} more)` : ''),
        total_affected: kept.length,
        health_flagged: kept.filter((d) => d.health && d.health.event_arn).length,
        ...(ss.cost_exposure ? { cost_exposure: rowExposure(kept, ss.cost_exposure) } : {}),
      },
    });
  }
  return { rows: out, stats };
};

// ---- suggestions: what the scan saw, ranked ----

export interface TagKeyInfo { key: string; resources: number; distinct: number }

export const tagKeys = (rows: DeprecationItem[]): TagKeyInfo[] => {
  const count = new Map<string, { resources: number; values: Set<string> }>();
  let total = 0;
  for (const row of rows) for (const d of detailsOf(row)) {
    total += 1;
    for (const [k, v] of Object.entries(d.tags || {})) {
      const e = count.get(k) || { resources: 0, values: new Set<string>() };
      e.resources += 1; e.values.add(String(v)); count.set(k, e);
    }
  }
  void total;
  return [...count.entries()]
    .map(([key, e]) => ({ key, resources: e.resources, distinct: e.values.size }))
    // keys shared by many resources with few distinct values (BU, Team) first;
    // one-value-per-resource keys (Name) last
    .sort((a, b) => (b.resources - b.distinct) - (a.resources - a.distinct) || b.resources - a.resources || a.key.localeCompare(b.key));
};

export const tagValues = (rows: DeprecationItem[], key: string): Array<{ value: string; resources: number }> => {
  const count = new Map<string, number>();
  let untagged = 0;
  for (const row of rows) for (const d of detailsOf(row)) {
    const v = d.tags?.[key];
    if (v === undefined) untagged += 1; else count.set(String(v), (count.get(String(v)) || 0) + 1);
  }
  const out = [...count.entries()].map(([value, resources]) => ({ value, resources })).sort((a, b) => b.resources - a.resources || a.value.localeCompare(b.value));
  if (untagged) out.push({ value: NOT_TAGGED, resources: untagged });
  return out;
};

export const resourceTotal = (rows: DeprecationItem[]): number =>
  rows.reduce((n, r) => n + (Number(r.service_specific?.total_affected) || detailsOf(r).length), 0);

// ---- "By tag" view: exposure per value of one key (the CTO's table) ----

export interface TagValueExposure { value: string; resources: number; attention: number; versions: number; monthly: number; forecast_12m: number }

export const exposureByTagValue = (rows: DeprecationItem[], key: string, isConcern: (status: string) => boolean): TagValueExposure[] => {
  const m = new Map<string, TagValueExposure & { rowIds: Set<string> }>();
  for (const row of rows) {
    const concern = isConcern(row.status);
    for (const d of detailsOf(row)) {
      const value = d.tags?.[key] ?? NOT_TAGGED;
      const e = m.get(value) || { value, resources: 0, attention: 0, versions: 0, monthly: 0, forecast_12m: 0, rowIds: new Set<string>() };
      e.resources += 1;
      if (concern) e.attention += 1;
      e.rowIds.add(row.item_id);
      const es = d.extended_support;
      if (es && es.eligible && es.monthly_yr1_2 !== undefined) { e.monthly += es.monthly_yr1_2; e.forecast_12m += es.forecast_12m || 0; }
      m.set(value, e);
    }
  }
  return [...m.values()]
    .map(({ rowIds, ...e }) => ({ ...e, versions: rowIds.size, monthly: Math.round(e.monthly * 100) / 100, forecast_12m: Math.round(e.forecast_12m * 100) / 100 }))
    // not tagged first (the conversation to have), then most exposed
    .sort((a, b) => (a.value === NOT_TAGGED ? -1 : b.value === NOT_TAGGED ? 1 : 0) || b.attention - a.attention || b.forecast_12m - a.forecast_12m || a.value.localeCompare(b.value));
};
