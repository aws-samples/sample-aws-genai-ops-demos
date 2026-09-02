import { describe, it, expect } from 'vitest';
import { determineLifecyclePhase, parseDate, applyTemporalEscalation } from '../engine';
import type { DeprecationItem } from '../../api';

// Helper to build a minimal DeprecationItem for testing
function makeItem(overrides: Partial<DeprecationItem> = {}): DeprecationItem {
  return {
    service_name: 'RDS',
    item_id: 'rds-mysql-5.7',
    status: 'deprecated',
    source_url: 'https://aws.amazon.com',
    extraction_date: '2024-01-01',
    last_verified: '2024-01-01',
    service_specific: {},
    ...overrides,
  };
}

describe('parseDate', () => {
  it('parses ISO 8601 date strings', () => {
    const d = parseDate('2024-06-15T00:00:00Z');
    expect(d).toBeInstanceOf(Date);
    expect(d!.toISOString()).toBe('2024-06-15T00:00:00.000Z');
  });

  it('parses YYYY-MM-DD format', () => {
    const d = parseDate('2024-06-15');
    expect(d).toBeInstanceOf(Date);
    expect(d!.getFullYear()).toBe(2024);
    expect(d!.getMonth()).toBe(5); // zero-indexed
    expect(d!.getDate()).toBe(15);
  });

  it('returns null for empty string', () => {
    expect(parseDate('')).toBeNull();
  });

  it('returns null for null/undefined', () => {
    expect(parseDate(null)).toBeNull();
    expect(parseDate(undefined)).toBeNull();
  });

  it('returns null for unparseable strings', () => {
    expect(parseDate('not-a-date')).toBeNull();
  });
});

describe('determineLifecyclePhase', () => {
  const refDate = new Date('2025-01-15T00:00:00Z');

  describe('date-based determination', () => {
    it('returns block_create_update when block_create_date is in the past', () => {
      const item = makeItem({
        service_specific: { block_create_date: '2024-12-01' },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'block_create_update', source: 'date' });
    });

    it('returns block_create_update when block_update_date is in the past', () => {
      const item = makeItem({
        service_specific: { block_update_date: '2024-11-01' },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'block_create_update', source: 'date' });
    });

    it('returns end_of_life when end_of_support_date is in the past', () => {
      const item = makeItem({
        service_specific: { end_of_support_date: '2024-06-01' },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'end_of_life', source: 'date' });
    });

    it('returns end_of_standard_support when end_of_standard_support_date is in the past', () => {
      const item = makeItem({
        service_specific: { end_of_standard_support_date: '2024-09-01' },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'end_of_standard_support', source: 'date' });
    });

    it('returns extended_support when end_of_extended_support_date exists (future)', () => {
      const item = makeItem({
        service_specific: { end_of_extended_support_date: '2026-06-01' },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'extended_support', source: 'date' });
    });

    it('returns extended_support when end_of_extended_support_date exists (past)', () => {
      const item = makeItem({
        service_specific: { end_of_extended_support_date: '2024-03-01' },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'extended_support', source: 'date' });
    });
  });

  describe('priority order', () => {
    it('block_create_update takes priority over end_of_life', () => {
      const item = makeItem({
        service_specific: {
          block_create_date: '2024-12-01',
          end_of_support_date: '2024-06-01',
        },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result.phase).toBe('block_create_update');
    });

    it('end_of_life takes priority over end_of_standard_support', () => {
      const item = makeItem({
        service_specific: {
          end_of_support_date: '2024-06-01',
          end_of_standard_support_date: '2024-03-01',
        },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result.phase).toBe('end_of_life');
    });

    it('end_of_standard_support takes priority over extended_support', () => {
      const item = makeItem({
        service_specific: {
          end_of_standard_support_date: '2024-09-01',
          end_of_extended_support_date: '2026-06-01',
        },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result.phase).toBe('end_of_standard_support');
    });
  });

  describe('status fallback', () => {
    it('maps end_of_life status to end_of_life phase', () => {
      const item = makeItem({ status: 'end_of_life', service_specific: {} });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'end_of_life', source: 'status' });
    });

    it('maps deprecated status to end_of_standard_support phase', () => {
      const item = makeItem({ status: 'deprecated', service_specific: {} });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'end_of_standard_support', source: 'status' });
    });

    it('maps extended_support status to extended_support phase', () => {
      const item = makeItem({ status: 'extended_support', service_specific: {} });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'extended_support', source: 'status' });
    });

    it('defaults unexpected status to end_of_standard_support (conservative)', () => {
      const item = makeItem({ status: 'unknown_status' as any, service_specific: {} });
      const result = determineLifecyclePhase(item, refDate);
      expect(result).toEqual({ phase: 'end_of_standard_support', source: 'status' });
    });
  });

  describe('edge cases', () => {
    it('handles missing service_specific gracefully', () => {
      const item = makeItem({ service_specific: undefined as any });
      const result = determineLifecyclePhase(item, refDate);
      expect(result.source).toBe('status');
    });

    it('ignores future block dates (not yet blocking)', () => {
      const item = makeItem({
        service_specific: { block_create_date: '2026-01-01' },
      });
      const result = determineLifecyclePhase(item, refDate);
      // Future block date shouldn't trigger block_create_update, falls through
      expect(result.phase).not.toBe('block_create_update');
    });

    it('ignores future end_of_support_date', () => {
      const item = makeItem({
        service_specific: { end_of_support_date: '2026-01-01' },
      });
      const result = determineLifecyclePhase(item, refDate);
      expect(result.phase).not.toBe('end_of_life');
    });
  });
});

describe('applyTemporalEscalation', () => {
  describe('null daysToMilestone (no escalation)', () => {
    it('returns base level unchanged when daysToMilestone is null', () => {
      expect(applyTemporalEscalation('faible', null)).toBe('faible');
      expect(applyTemporalEscalation('moyen', null)).toBe('moyen');
      expect(applyTemporalEscalation('élevé', null)).toBe('élevé');
      expect(applyTemporalEscalation('critique', null)).toBe('critique');
    });
  });

  describe('de-minimis rule (beyond 365 days)', () => {
    it('returns faible when days > 365 regardless of base level', () => {
      expect(applyTemporalEscalation('critique', 366)).toBe('faible');
      expect(applyTemporalEscalation('élevé', 500)).toBe('faible');
      expect(applyTemporalEscalation('moyen', 1000)).toBe('faible');
      expect(applyTemporalEscalation('faible', 400)).toBe('faible');
    });
  });

  describe('escalation within 90 days', () => {
    it('escalates faible to moyen', () => {
      expect(applyTemporalEscalation('faible', 90)).toBe('moyen');
      expect(applyTemporalEscalation('faible', 30)).toBe('moyen');
      expect(applyTemporalEscalation('faible', 1)).toBe('moyen');
    });

    it('escalates moyen to élevé', () => {
      expect(applyTemporalEscalation('moyen', 90)).toBe('élevé');
      expect(applyTemporalEscalation('moyen', 45)).toBe('élevé');
    });

    it('escalates élevé to critique', () => {
      expect(applyTemporalEscalation('élevé', 90)).toBe('critique');
      expect(applyTemporalEscalation('élevé', 10)).toBe('critique');
    });

    it('never exceeds critique (stays at critique)', () => {
      expect(applyTemporalEscalation('critique', 90)).toBe('critique');
      expect(applyTemporalEscalation('critique', 1)).toBe('critique');
    });
  });

  describe('no change between 90 and 365 days', () => {
    it('returns base level unchanged when days between 91 and 365', () => {
      expect(applyTemporalEscalation('faible', 91)).toBe('faible');
      expect(applyTemporalEscalation('moyen', 200)).toBe('moyen');
      expect(applyTemporalEscalation('élevé', 365)).toBe('élevé');
      expect(applyTemporalEscalation('critique', 300)).toBe('critique');
    });
  });

  describe('boundary values', () => {
    it('exactly 90 days triggers escalation', () => {
      expect(applyTemporalEscalation('faible', 90)).toBe('moyen');
    });

    it('exactly 365 days does NOT trigger de-minimis (boundary is >365)', () => {
      expect(applyTemporalEscalation('élevé', 365)).toBe('élevé');
    });

    it('0 days triggers escalation', () => {
      expect(applyTemporalEscalation('faible', 0)).toBe('moyen');
    });
  });
});
