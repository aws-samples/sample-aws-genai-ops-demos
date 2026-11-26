import { describe, expect, it } from 'vitest';
import sample from '../../../sample-data/anycompany-data.json';
import { genericEntries, tabKind, tabList } from './tabs';
import type { Report } from './types';

const report = sample as unknown as Report;

describe('tabList', () => {
  it('keeps the data order and each tab label', () => {
    const list = tabList(report.tabs);
    expect(list.map((t) => t.label)).toEqual(report.tabs.map((t) => t.label));
    expect(list.map((t) => t.kind)).toEqual([
      'instructions', 'cards', 'operational-review', 'recommendations', 'inconsistencies', 'governance', 'cost-optimization',
    ]);
  });
  it('never drops an unknown tab: it becomes generic', () => {
    const list = tabList([{ id: 'x', label: 'Extra', type: 'future' } as never]);
    expect(list).toHaveLength(1);
    expect(list[0].kind).toBe('generic');
  });
  it('falls back to the id when a label is missing and tolerates missing tabs', () => {
    expect(tabList([{ id: 'x', type: 'cards' } as never])[0].label).toBe('x');
    expect(tabList(undefined)).toEqual([]);
  });
});

describe('tabKind', () => {
  it('recognises the seven types', () => {
    expect(tabKind('governance')).toBe('governance');
    expect(tabKind('nope')).toBe('generic');
    expect(tabKind(undefined)).toBe('generic');
  });
});

describe('genericEntries', () => {
  it('flattens values to text, skipping id, label and type', () => {
    const e = genericEntries({ id: 'a', label: 'A', type: 't', n: 3, s: 'x', ok: true, none: null, list: ['p', 'q'], obj: { k: 1 } });
    expect(Object.fromEntries(e.map((x) => [x.key, x.text]))).toEqual({
      n: '3', s: 'x', ok: 'true', none: 'Unconfirmed', list: 'p, q', obj: '{"k":1}',
    });
  });
});
