import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isExcluded, loadExclude, parsePatterns, saveExclude } from './exclude';

describe('parsePatterns', () => {
  it('splits on commas, trims and lower-cases', () => {
    expect(parsePatterns(' CV-*, *Commvault* ,logs ,')).toEqual(['cv-*', '*commvault*', 'logs']);
  });
  it('returns an empty list for empty text', () => {
    expect(parsePatterns('')).toEqual([]);
    expect(parsePatterns(' , ')).toEqual([]);
  });
});

describe('isExcluded', () => {
  const p = parsePatterns('cv-*, *commvault*, logs');
  it('treats * as a wildcard over the whole name', () => {
    expect(isExcluded('cv-backup-01', p)).toBe(true);
    expect(isExcluded('CV-Backup', p)).toBe(true);
    expect(isExcluded('my-cv-backup', p)).toBe(false);
    expect(isExcluded('acme-commvault-store', p)).toBe(true);
  });
  it('matches a term without * when contained', () => {
    expect(isExcluded('app-logs-prod', p)).toBe(true);
    expect(isExcluded('analytics', p)).toBe(false);
  });
  it('excludes nothing without patterns and escapes regex characters', () => {
    expect(isExcluded('anything', [])).toBe(false);
    expect(isExcluded('a.b', ['a.b'])).toBe(true);
    expect(isExcluded('axb', ['a.b'])).toBe(false);
    expect(isExcluded('a+b', ['a+*'])).toBe(true);
  });
});

describe('isExcluded performance', () => {
  it('stays fast on a pathological pattern and a 63-character name', () => {
    const name = 'a'.repeat(63);
    const t = performance.now();
    expect(isExcluded(name, ['*a*a*a*a*a*a*b'])).toBe(false);
    expect(isExcluded(name, ['*a*a*a*a*a*a*a'])).toBe(true);
    expect(performance.now() - t).toBeLessThan(50);
  });
  it('handles ordering, overlap and edge wildcards', () => {
    expect(isExcluded('abc', ['a*c'])).toBe(true);
    expect(isExcluded('ac', ['a*c'])).toBe(true);
    expect(isExcluded('a', ['a*a'])).toBe(false);
    expect(isExcluded('xaybz', ['*a*b*'])).toBe(true);
    expect(isExcluded('xbyaz', ['*a*b*'])).toBe(false);
    expect(isExcluded('anything', ['*'])).toBe(true);
    expect(isExcluded('abab', ['ab*ab'])).toBe(true);
    expect(isExcluded('aba', ['ab*ba'])).toBe(false);
  });
});

describe('localStorage persistence', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('does not throw when localStorage is absent', () => {
    vi.stubGlobal('localStorage', undefined);
    expect(() => saveExclude('sample', 'logs')).not.toThrow();
    expect(loadExclude('sample')).toBe('');
  });

  it('does not throw when localStorage throws', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('denied'); },
      setItem: () => { throw new Error('denied'); },
    });
    expect(() => saveExclude('r1', 'x')).not.toThrow();
    expect(loadExclude('r1')).toBe('');
  });

  describe('with a working store', () => {
    const store = new Map<string, string>();
    beforeEach(() => {
      store.clear();
      vi.stubGlobal('localStorage', {
        getItem: (k: string) => store.get(k) ?? null,
        setItem: (k: string, v: string) => void store.set(k, v),
      });
    });
    it('round-trips under s3dp-exclude:<reportKey>', () => {
      saveExclude('run-1', 'cv-*, logs');
      expect(store.get('s3dp-exclude:run-1')).toBe('cv-*, logs');
      expect(loadExclude('run-1')).toBe('cv-*, logs');
      expect(loadExclude('sample')).toBe('');
    });
  });
});
