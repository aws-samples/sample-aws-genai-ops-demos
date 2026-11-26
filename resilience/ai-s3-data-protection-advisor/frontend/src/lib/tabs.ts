import type { Tab } from './types';

export type TabKind =
  | 'instructions' | 'cards' | 'operational-review' | 'recommendations'
  | 'inconsistencies' | 'governance' | 'cost-optimization' | 'generic';

const KINDS: TabKind[] = [
  'instructions', 'cards', 'operational-review', 'recommendations',
  'inconsistencies', 'governance', 'cost-optimization',
];

export function tabKind(type: string | undefined): TabKind {
  return (KINDS as string[]).includes(type ?? '') ? (type as TabKind) : 'generic';
}

export interface TabEntry {
  id: string;
  label: string;
  kind: TabKind;
  tab: Tab;
}

/** Tabs in data order; an unknown type is kept as a generic tab, never dropped. */
export function tabList(tabs: Tab[] | undefined | null): TabEntry[] {
  return (tabs ?? []).map((tab, i) => {
    const t = tab as unknown as { id?: string; label?: string; type?: string };
    const id = t.id ?? `tab-${i}`;
    return { id, label: t.label || id, kind: tabKind(t.type), tab };
  });
}

function text(v: unknown): string {
  if (v === null || v === undefined) return 'Unconfirmed';
  if (Array.isArray(v)) return v.map(text).join(', ');
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function genericEntries(tab: object): { key: string; text: string }[] {
  return Object.entries(tab)
    .filter(([k]) => !['id', 'label', 'type'].includes(k))
    .map(([key, v]) => ({ key, text: text(v) }));
}
