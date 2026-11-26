import { describe, expect, it } from 'vitest';
import { accountLabel, formatDate, formatDuration, riskName } from './format';

describe('formatDate', () => {
  it('formats in UTC', () => {
    expect(formatDate('2026-10-01T09:05:59Z')).toBe('2026-10-01 09:05 UTC');
    expect(formatDate('2026-10-01T23:30:00+02:00')).toBe('2026-10-01 21:30 UTC');
  });
  it('returns an empty string for missing or invalid input', () => {
    expect(formatDate(undefined)).toBe('');
    expect(formatDate('not a date')).toBe('');
  });
});

describe('formatDuration', () => {
  it('formats seconds, minutes and hours', () => {
    expect(formatDuration('2026-10-01T09:00:00Z', '2026-10-01T09:00:42Z')).toBe('42s');
    expect(formatDuration('2026-10-01T09:00:00Z', '2026-10-01T09:07:05Z')).toBe('7m 5s');
    expect(formatDuration('2026-10-01T09:00:00Z', '2026-10-01T10:02:00Z')).toBe('1h 2m');
  });
  it('is empty while running or on bad input', () => {
    expect(formatDuration('2026-10-01T09:00:00Z', undefined)).toBe('');
    expect(formatDuration('x', 'y')).toBe('');
    expect(formatDuration('2026-10-01T09:00:00Z', '2026-10-01T08:00:00Z')).toBe('');
  });
});

describe('riskName', () => {
  it('names the three risks', () => {
    expect(riskName('he')).toBe('Human error');
    expect(riskName('ma')).toBe('Malicious activity');
    expect(riskName('rd')).toBe('Regional disruption');
  });
});

describe('accountLabel', () => {
  it('shows the id once when there is no alias', () => {
    expect(accountLabel('111122223333', '111122223333')).toBe('111122223333');
    expect(accountLabel('', '111122223333')).toBe('111122223333');
    expect(accountLabel(undefined, '111122223333')).toBe('111122223333');
  });
  it('shows name and id otherwise', () => {
    expect(accountLabel('Production', '111122223333')).toBe('Production (111122223333)');
    expect(accountLabel('Production', '')).toBe('Production');
  });
});
