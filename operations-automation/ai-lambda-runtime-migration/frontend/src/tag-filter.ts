// Tag helpers for the inventory table's PropertyFilter. The scan discovers the
// whole org unfiltered; the operator narrows the results in the table by
// region and by tag. These pure helpers turn the tags present on the loaded
// rows into PropertyFilter properties and options, and match a row against a
// chosen tag. Adapted from aws-services-lifecycle-tracker to the FLAT inventory
// of this demo: one Lambda function = one MigrationFunction row carrying its
// own tags map.
//
// Model = the Resource Groups Tagging API's TagFilter shape: { key, values[] }.
// Within a token any listed value matches; a key without values means "tagged
// with this key"; (not tagged) means "without it".
import type { MigrationFunction } from './api';

export interface TagFilter { key: string; values: string[] }

export const NOT_TAGGED = '(not tagged)';

// ---- matching ----

export const matchesTags = (tags: Record<string, string> | undefined, filters: TagFilter[]): boolean =>
  filters.length === 0 || filters.some((f) => {
    if (f.values.includes(NOT_TAGGED)) return !tags || !(f.key in tags);
    if (!tags || !(f.key in tags)) return false;
    return f.values.length === 0 || f.values.includes(tags[f.key]);
  });

// ---- suggestions: the tags present on the loaded rows, ranked ----

export interface TagKeyInfo { key: string; resources: number; distinct: number }

export const tagKeys = (rows: MigrationFunction[]): TagKeyInfo[] => {
  const count = new Map<string, { resources: number; values: Set<string> }>();
  for (const row of rows) {
    for (const [k, v] of Object.entries(row.tags || {})) {
      const e = count.get(k) || { resources: 0, values: new Set<string>() };
      e.resources += 1; e.values.add(String(v)); count.set(k, e);
    }
  }
  return [...count.entries()]
    .map(([key, e]) => ({ key, resources: e.resources, distinct: e.values.size }))
    // keys shared by many functions with few distinct values (BU, Team) first;
    // one-value-per-function keys (Name) last
    .sort((a, b) => (b.resources - b.distinct) - (a.resources - a.distinct) || b.resources - a.resources || a.key.localeCompare(b.key));
};

export const tagValues = (rows: MigrationFunction[], key: string): Array<{ value: string; resources: number }> => {
  const count = new Map<string, number>();
  let untagged = 0;
  for (const row of rows) {
    const v = row.tags?.[key];
    if (v === undefined) untagged += 1; else count.set(String(v), (count.get(String(v)) || 0) + 1);
  }
  const out = [...count.entries()].map(([value, resources]) => ({ value, resources })).sort((a, b) => b.resources - a.resources || a.value.localeCompare(b.value));
  if (untagged) out.push({ value: NOT_TAGGED, resources: untagged });
  return out;
};
