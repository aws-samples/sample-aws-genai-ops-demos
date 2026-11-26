import { describe, expect, it } from 'vitest';
import sample from '../../../sample-data/anycompany-data.json';

// Every key path in the sample, per tab type, must be listed here as rendered or ignored.
// A new field in data.json fails this test until someone decides how to show it.

function paths(v: unknown, p: string, out: Set<string>) {
  if (Array.isArray(v)) v.forEach((x) => paths(x, p + '[]', out));
  else if (v && typeof v === 'object') Object.entries(v).forEach(([k, x]) => paths(x, `${p}.${k}`, out));
  else out.add(p);
}

const COMMON_IGNORED = ['.id', '.label', '.type'];

const RENDERED: Record<string, string[]> = {
  instructions: ['.heading', '.intro', '.sections[].items[]', '.sections[].note', '.sections[].table.columns[]', '.sections[].table.rows[][]', '.sections[].title'],
  cards: [
    '.row.account', '.row.account_id', '.row.action', '.row.bucket', '.row.controls[].cells.he[]', '.row.controls[].cells.ma[]',
    '.row.controls[].cells.rd[]', '.row.controls[].control', '.row.controls[].value', '.row.evidence[].label', '.row.evidence[].value',
    '.row.findings[].description', '.row.findings[].severity', '.row.findings[].title', '.row.he', '.row.ma', '.row.rd',
    '.row.reasons[]', '.row.region', '.row.req[]', '.row.role', '.row.score', '.row.size_bytes', '.row.size_human', '.row.spotlight',
    '.row.status', '.row.status_color', '.row.tier', '.row.vce',
  ],
  'operational-review': [
    '.description', '.resources[].name', '.resources[].pillars[].checks[].description', '.resources[].pillars[].checks[].label',
    '.resources[].pillars[].checks[].pass', '.resources[].pillars[].checks[].severity', '.resources[].pillars[].name',
    '.resources[].pillars[].pass', '.resources[].pillars[].total', '.resources[].region',
  ],
  recommendations: [
    '.items[].account_name', '.items[].bucket', '.items[].detail', '.items[].inconsistency', '.items[].recommendation',
    '.items[].region', '.items[].severity', '.items[].size_human', '.items[].why',
  ],
  inconsistencies: [
    '.consistent_families[].family', '.consistent_families[].note', '.families_compared', '.items[].align_to', '.items[].basis',
    '.items[].family', '.items[].members[].account', '.items[].members[].bucket', '.items[].members[].region',
    '.items[].members[].value', '.items[].recommendation', '.items[].setting', '.items[].severity', '.items[].weaker[]', '.note',
  ],
  governance: [
    '.dimensions[].current', '.dimensions[].evidence[]', '.dimensions[].level', '.dimensions[].name', '.dimensions[].next',
    '.findings[].dimension', '.findings[].evidence', '.findings[].improvement', '.findings[].severity', '.findings[].title',
    '.levels[].description', '.levels[].label', '.levels[].level', '.overall', '.overall_label', '.overall_level',
    '.policies[].compliant', '.policies[].failing[]', '.policies[].pct', '.policies[].rule', '.policies[].scope',
    '.policies[].total', '.policies[].why', '.summary',
  ],
  'cost-optimization': [
    '.items[].basis', '.items[].description', '.items[].effort', '.items[].magnitude.label', '.items[].resources[]',
    '.items[].savings', '.items[].title',
  ],
};

// Deliberately not shown: duplicates of rendered fields, or numbers the report text already states
// (the dashboard adds no figures of its own to cost estimates).
const IGNORED: Record<string, string[]> = {
  instructions: [],
  cards: [
    '.badge.color', '.badge.text', '.details[].label', '.details[].value', '.recommendations[].description',
    '.recommendations[].severity', '.recommendations[].title', '.row.chips[]', '.row.gap[]', '.row.rec[]', '.row.size_short',
    '.row.third_party', '.row.vce_usd', '.sortValue', '.subtitle', '.title',
  ],
  'operational-review': ['.pillars[]'],
  recommendations: [
    '.items[].account', '.items[].description', '.items[].priority', '.items[].resource', '.items[].size_bytes',
    '.items[].status', '.items[].tier', '.items[].title',
  ],
  inconsistencies: ['.items[].match', '.items[].relation'],
  governance: ['.dimensions[].id'],
  'cost-optimization': [
    '.items[].annual_usd', '.items[].estimatedSaving', '.items[].impact', '.items[].magnitude.level', '.items[].monthly_usd',
    '.items[].one_time_usd', '.items[].up_to', '.items[].worst_case_usd',
  ],
};

describe('sample fields', () => {
  const tabs = (sample as unknown as { tabs: { type: string; cards?: unknown[] }[] }).tabs;
  for (const tab of tabs) {
    it(`every key of a "${tab.type}" tab is rendered or deliberately ignored`, () => {
      const out = new Set<string>();
      if (tab.type === 'cards') (tab.cards ?? []).forEach((c) => paths(c, '', out));
      else paths(tab, '', out);
      const known = new Set([...(RENDERED[tab.type] ?? []), ...(IGNORED[tab.type] ?? []), ...COMMON_IGNORED]);
      const unknown = [...out].filter((p) => !known.has(p));
      expect(unknown, `unhandled fields in ${tab.type}`).toEqual([]);
    });
  }

  it('every allowlisted path exists in the sample (no stale entries)', () => {
    for (const tab of tabs) {
      const out = new Set<string>();
      if (tab.type === 'cards') (tab.cards ?? []).forEach((c) => paths(c, '', out));
      else paths(tab, '', out);
      for (const p of [...(RENDERED[tab.type] ?? []), ...(IGNORED[tab.type] ?? [])]) {
        expect(out.has(p), `${tab.type} ${p}`).toBe(true);
      }
    }
  });

  it('meta keys are known', () => {
    const meta = Object.keys((sample as unknown as { meta: object }).meta);
    const known = ['account', 'ai', 'audience', 'customerName', 'generated', 'sample', 'service'];
    expect(meta.filter((k) => !known.includes(k))).toEqual([]);
  });
});
