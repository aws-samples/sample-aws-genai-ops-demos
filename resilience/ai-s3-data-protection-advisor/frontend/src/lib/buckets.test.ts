import { describe, expect, it } from 'vitest';
import sample from '../../../sample-data/anycompany-data.json';
import { emptyFilter, filterRows, formatBytes, middleTruncate, options, summary } from './buckets';
import { parsePatterns } from './exclude';
import type { BucketRow, CardsTab, Report } from './types';

const rows: BucketRow[] = ((sample as unknown as Report).tabs.find((t) => t.type === 'cards') as CardsTab).cards.map((c) => c.row);
const f = (over: Partial<ReturnType<typeof emptyFilter>>) => ({ ...emptyFilter(), ...over });

describe('formatBytes', () => {
  it('uses binary units', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1024)).toBe('1.0 KiB');
    expect(formatBytes(1024 ** 5 * 20)).toBe('20 PiB');
    expect(formatBytes(1536 * 1024 ** 3)).toBe('1.5 TiB');
  });
});

describe('filterRows', () => {
  it('returns every row with an empty filter', () => {
    expect(filterRows(rows, emptyFilter())).toHaveLength(rows.length);
  });

  it('exclude patterns remove matching buckets from rows and summary', () => {
    const patterns = parsePatterns('*invoices*, logs');
    const out = filterRows(rows, f({ patterns }));
    const gone = rows.filter((r) => /invoices/i.test(r.bucket) || r.bucket.toLowerCase().includes('logs'));
    expect(gone.length).toBeGreaterThan(0);
    expect(out).toHaveLength(rows.length - gone.length);
    expect(summary(out).buckets).toBe(rows.length - gone.length);
  });

  it('cv-* style patterns match from the start only', () => {
    const fake = [{ ...rows[0], bucket: 'cv-store' }, { ...rows[1], bucket: 'my-cv-store' }];
    expect(filterRows(fake, f({ patterns: ['cv-*'] })).map((r) => r.bucket)).toEqual(['my-cv-store']);
  });

  it('priority focus toggle keeps spotlight rows only', () => {
    const out = filterRows(rows, f({ spotlightOnly: true }));
    expect(out.length).toBe(rows.filter((r) => r.spotlight).length);
    expect(out.every((r) => r.spotlight)).toBe(true);
  });

  it('selects filter on account, region, role, status and tier', () => {
    const r0 = rows[0];
    for (const [k, v] of [['region', r0.region], ['role', r0.role], ['status', r0.status], ['tier', r0.tier], ['account', r0.account]] as const) {
      const out = filterRows(rows, f({ [k]: v }));
      expect(out.length).toBeGreaterThan(0);
      expect(out.every((r) => r[k] === v)).toBe(true);
    }
  });

  it('text filter searches bucket, account, region, role, action and reasons, case-insensitively', () => {
    expect(filterRows(rows, f({ text: rows[0].bucket.toUpperCase() })).map((r) => r.bucket)).toContain(rows[0].bucket);
    const reason = rows.find((r) => r.reasons?.length)!.reasons[0];
    expect(filterRows(rows, f({ text: reason.slice(0, 8) })).length).toBeGreaterThan(0);
    expect(filterRows(rows, f({ text: 'zzz-no-such-thing' }))).toEqual([]);
  });

  it('tolerates rows with missing optional arrays', () => {
    const odd = [{ ...rows[0], reasons: undefined, req: undefined }] as unknown as BucketRow[];
    expect(() => filterRows(odd, f({ text: 'x' }))).not.toThrow();
    expect(() => summary(odd)).not.toThrow();
  });
});

describe('summary', () => {
  it('counts buckets, bytes and priority focus', () => {
    const s = summary(rows);
    expect(s.buckets).toBe(rows.length);
    expect(s.totalBytes).toBe(rows.reduce((a, r) => a + r.size_bytes, 0));
    expect(s.spotlight).toBe(rows.filter((r) => r.spotlight).length);
  });

  it('counts unprotected-required per risk from req', () => {
    const s = summary(rows);
    for (const k of ['he', 'ma', 'rd'] as const) {
      const exp = rows.filter((r) => (r.req ?? []).includes(({ he: 'human_error', ma: 'malicious_activity', rd: 'regional_disruption' })[k]) && r[k] === 'unprotected').length;
      expect(s.unprotectedRequired[k]).toBe(exp);
    }
  });

  it('counts unknown values as unconfirmed, not unprotected', () => {
    const row = { ...rows[0], req: ['regional_disruption'], rd: 'unknown' } as BucketRow;
    const s = summary([row]);
    expect(s.unprotectedRequired.rd).toBe(0);
    expect(s.unconfirmedRequired.rd).toBe(1);
    const odd = { ...rows[0], req: ['regional_disruption'], rd: 'NotVerified' } as BucketRow;
    expect(summary([odd]).unconfirmedRequired.rd).toBe(1);
  });
});

describe('options', () => {
  it('lists sorted unique values', () => {
    const o = options(rows, 'region');
    expect(o).toEqual([...new Set(rows.map((r) => r.region))].sort());
  });
});

describe('middleTruncate', () => {
  it('keeps short names whole', () => {
    expect(middleTruncate('short-bucket')).toBe('short-bucket');
  });
  it('keeps the first 10 and last 18 characters of a long name', () => {
    const n = 's3dp-111122223333-eu-west-1-backups-primary-0001';
    const t = middleTruncate(n);
    expect(t).toBe(`${n.slice(0, 10)}\u2026${n.slice(-18)}`);
    expect(t.endsWith('primary-0001')).toBe(true);
    expect(t.length).toBe(29);
  });
});
