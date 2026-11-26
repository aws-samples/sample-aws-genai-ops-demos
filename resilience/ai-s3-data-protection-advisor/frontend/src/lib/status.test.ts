import { describe, expect, it } from 'vitest';
import sample from '../../../sample-data/anycompany-data.json';
import { aiUsed, unconfirmedText, bucketStatus, cellStatus, protectionStatus, reportNotice, severityType, AI_NOTICE, RULES_NOTICE } from './status';
import type { BucketRow, CardsTab, Report } from './types';

const report = sample as unknown as Report;
const rows: BucketRow[] = (report.tabs.find((t) => t.type === 'cards') as CardsTab).cards.map((c) => c.row);

describe('protectionStatus', () => {
  it.each([
    ['protected', 'success', 'Protected'],
    ['protected_governance', 'success', 'Protected: Object Lock governance mode (can be bypassed)'],
    ['protected_lock', 'success', 'Protected: Object Lock per-object retention'],
    ['likely', 'info', 'Likely protected: to confirm'],
    ['partial', 'warning', 'Partial'],
    ['unprotected', 'error', 'Unprotected'],
    ['unknown', 'pending', 'Unconfirmed'],
  ])('%s', (state, type, label) => {
    expect(protectionStatus(state)).toEqual({ type, label });
  });

  it('treats null, undefined and unrecognised values as Unconfirmed, never None or Off', () => {
    for (const v of [null, undefined, '', 'NotVerified', 'whatever']) {
      expect(protectionStatus(v as string)).toEqual({ type: 'pending', label: 'Unconfirmed' });
    }
  });

  it('maps every he/ma/rd value in the sample to a known (non-fallback) entry', () => {
    const known = ['protected', 'protected_governance', 'protected_lock', 'likely', 'partial', 'unprotected', 'unknown'];
    const seen = new Set(rows.flatMap((r) => [r.he, r.ma, r.rd]));
    for (const v of seen) expect(known, `new protection value: ${v}`).toContain(v);
  });
});

describe('cellStatus', () => {
  it('maps the five cell states', () => {
    expect(cellStatus('ok').type).toBe('success');
    expect(cellStatus('missing').type).toBe('error');
    expect(cellStatus('partial').type).toBe('warning');
    expect(cellStatus('na')).toEqual({ type: 'stopped', label: 'Not applicable' });
    expect(cellStatus('unknown')).toEqual({ type: 'pending', label: 'Unconfirmed' });
  });

  it('falls back to Unconfirmed for anything else', () => {
    expect(cellStatus(null as unknown as string)).toEqual({ type: 'pending', label: 'Unconfirmed' });
    expect(cellStatus('NotVerified')).toEqual({ type: 'pending', label: 'Unconfirmed' });
  });

  it('knows every cell state in the sample (a new value fails here)', () => {
    const known = ['ok', 'missing', 'partial', 'na', 'unknown'];
    const seen = new Set(rows.flatMap((r) => r.controls.flatMap((c) => Object.values(c.cells).map((cell) => cell[0]))));
    for (const v of seen) expect(known, `new cell state: ${v}`).toContain(v);
  });
});

describe('bucketStatus', () => {
  const row = (status_color: string, status = 'LABEL') => ({ status_color, status }) as BucketRow;
  it('maps status_color and keeps row.status as the label', () => {
    expect(bucketStatus(row('red'))).toEqual({ type: 'error', label: 'LABEL' });
    expect(bucketStatus(row('orange')).type).toBe('warning');
    expect(bucketStatus(row('yellow')).type).toBe('warning');
    expect(bucketStatus(row('green')).type).toBe('success');
    expect(bucketStatus(row('grey')).type).toBe('pending');
    expect(bucketStatus(row('mauve')).type).toBe('pending');
  });

  it('knows every status_color in the sample', () => {
    const known = ['red', 'orange', 'yellow', 'green', 'grey'];
    for (const r of rows) expect(known).toContain(r.status_color);
  });
});

describe('severityType', () => {
  it('maps severities', () => {
    expect(severityType('HIGH')).toBe('error');
    expect(severityType('MEDIUM')).toBe('warning');
    expect(severityType('LOW')).toBe('info');
    expect(severityType('other')).toBe('info');
  });
});

describe('aiUsed and reportNotice', () => {
  it('is true when classification ran or the narrative is pending, complete or partial', () => {
    expect(aiUsed({ ai: { classification: true, narrative: 'off' } })).toBe(true);
    for (const n of ['pending', 'complete', 'partial']) {
      expect(aiUsed({ ai: { classification: false, narrative: n } })).toBe(true);
    }
  });

  it('is false for off, unavailable and a missing ai block', () => {
    expect(aiUsed({ ai: { classification: false, narrative: 'off' } })).toBe(false);
    expect(aiUsed({ ai: { classification: false, narrative: 'unavailable' } })).toBe(false);
    expect(aiUsed({})).toBe(false);
    expect(aiUsed({ ai: null })).toBe(false);
    expect(aiUsed(undefined)).toBe(false);
  });

  it('picks the matching notice', () => {
    expect(reportNotice({ ai: { classification: true, narrative: 'complete' } })).toBe(AI_NOTICE);
    expect(reportNotice({})).toBe(RULES_NOTICE);
    expect(reportNotice(report.meta)).toBe(RULES_NOTICE);
    expect(RULES_NOTICE.startsWith('Rules-based report: no generative AI was used.')).toBe(true);
  });
});

describe('unconfirmedText', () => {
  it('renders unknown-like values as Unconfirmed', () => {
    for (const v of ['unknown', 'Unknown', 'not verified', 'Not verified', 'NotVerified', '', null, undefined]) {
      expect(unconfirmedText(v as string)).toBe('Unconfirmed');
    }
  });
  it('keeps real values', () => {
    expect(unconfirmedText('Enabled')).toBe('Enabled');
    expect(unconfirmedText('None')).toBe('None');
  });
});
