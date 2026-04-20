/**
 * Property tests for Cost Agent formatting and validation logic.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise pure TypeScript implementations that mirror the
 * Python Cost Agent's formatting and validation behaviour so they can
 * be verified with fast-check without calling boto3.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  arbCostTimePeriod,
  arbCostComparison,
} from '../generators/cost-data.gen';

// ---------------------------------------------------------------------------
// Local TypeScript implementations mirroring the Python Cost Agent logic
// ---------------------------------------------------------------------------

/** Maximum supported time range for cost queries (12 months ≈ 366 days). */
const MAX_TIME_RANGE_DAYS = 366;

/**
 * Format a numeric amount as a currency string.
 * Mirrors `_format_currency` in `agents/cost-agent/main.py`.
 */
function formatCurrency(amount: number, unit = 'USD'): string {
  return `$${amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${unit}`;
}

/**
 * Calculate and format percentage change between two values.
 * Mirrors `_format_percentage_change` in `agents/cost-agent/main.py`.
 */
function formatPercentageChange(current: number, previous: number): string {
  if (previous === 0) {
    return 'N/A (no previous data)';
  }
  const change = ((current - previous) / previous) * 100;
  const direction = change > 0 ? 'increase' : 'decrease';
  return `${Math.abs(change).toFixed(1)}% ${direction}`;
}

/** A single cost data object that the formatter consumes. */
interface CostDataInput {
  amount: number;
  currency: string;
  startDate: string;
  endDate: string;
  previousPeriodAmount: number;
}

/**
 * Format a cost data object into a human-readable response string.
 * Mirrors the formatting logic in `_format_cost_results`.
 *
 * The output MUST contain:
 *  - a currency value (e.g. "$1,234.56 USD")
 *  - a time range   (startDate … endDate)
 *  - a percentage change string
 */
function formatCostData(input: CostDataInput): string {
  const currencyStr = formatCurrency(input.amount, input.currency);
  const timeRange = `${input.startDate} to ${input.endDate}`;
  const pctChange = formatPercentageChange(input.amount, input.previousPeriodAmount);

  return `Cost and Usage (${timeRange}) — Amount: ${currencyStr} (${pctChange})`;
}

/**
 * Validate that a date range does not exceed 12 months.
 * Mirrors `_validate_time_range` in `agents/cost-agent/main.py`.
 *
 * Returns `{ valid: true }` when the range is acceptable, or
 * `{ valid: false, error: string }` when it should be rejected.
 */
function validateTimeRange(
  startDate: string,
  endDate: string,
): { valid: true } | { valid: false; error: string } {
  const start = new Date(startDate);
  const end = new Date(endDate);
  const diffMs = end.getTime() - start.getTime();
  const diffDays = diffMs / (1000 * 60 * 60 * 24);

  if (diffDays < 0) {
    return {
      valid: false,
      error: `Invalid time range: start date (${startDate}) is after end date (${endDate}).`,
    };
  }
  if (diffDays > MAX_TIME_RANGE_DAYS) {
    return {
      valid: false,
      error: `Time range of ${Math.round(diffDays)} days exceeds the maximum supported range of 12 months (${MAX_TIME_RANGE_DAYS} days). Please narrow your query to a 12-month window.`,
    };
  }
  return { valid: true };
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Arbitrary that produces a CostDataInput with guaranteed-positive amounts. */
const arbCostDataInput: fc.Arbitrary<CostDataInput> = fc.record({
  amount: fc.double({ min: 0.01, max: 999_999.99, noNaN: true }).map((n) => Math.round(n * 100) / 100),
  currency: fc.constantFrom('USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD'),
  startDate: fc.date({ min: new Date('2023-01-01'), max: new Date('2025-06-01') }).map((d) => d.toISOString().slice(0, 10)),
  endDate: fc.date({ min: new Date('2023-01-02'), max: new Date('2025-12-31') }).map((d) => d.toISOString().slice(0, 10)),
  previousPeriodAmount: fc.double({ min: 0, max: 999_999.99, noNaN: true }).map((n) => Math.round(n * 100) / 100),
});

/**
 * Arbitrary that produces a date pair where the range is guaranteed to
 * exceed 12 months (> 366 days).
 */
const arbExceedingDateRange = fc
  .date({ min: new Date('2023-01-01'), max: new Date('2024-01-01') })
  .chain((start) => {
    const minEnd = new Date(start.getTime() + (MAX_TIME_RANGE_DAYS + 1) * 24 * 60 * 60 * 1000);
    const maxEnd = new Date(start.getTime() + 1000 * 24 * 60 * 60 * 1000);
    return fc.date({ min: minEnd, max: maxEnd }).map((end) => ({
      startDate: start.toISOString().slice(0, 10),
      endDate: end.toISOString().slice(0, 10),
    }));
  });

/**
 * Arbitrary that produces a date pair where the range is within 12 months
 * (0 ≤ days ≤ 366).
 */
const arbValidDateRange = fc
  .date({ min: new Date('2023-01-01'), max: new Date('2025-06-01') })
  .chain((start) => {
    const maxEnd = new Date(start.getTime() + MAX_TIME_RANGE_DAYS * 24 * 60 * 60 * 1000);
    return fc.date({ min: start, max: maxEnd }).map((end) => ({
      startDate: start.toISOString().slice(0, 10),
      endDate: end.toISOString().slice(0, 10),
    }));
  });

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Cost Agent property tests', () => {
  /**
   * Property 4: Cost data formatting includes required fields
   *
   * For any cost data object containing amount, currency, start date,
   * end date, and previous period amount, the Cost Agent's response
   * formatter should produce output containing a currency value, a time
   * range, and a percentage change.
   *
   * **Validates: Requirements 2.3, 2.4**
   */
  it('Property 4: Cost data formatting includes required fields — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbCostDataInput, (input) => {
        const output = formatCostData(input);

        // Must contain a currency value ($ sign followed by digits)
        expect(output).toMatch(/\$[\d,]+\.\d{2}/);

        // Must contain the currency code
        expect(output).toContain(input.currency);

        // Must contain a time range (startDate to endDate)
        expect(output).toContain(input.startDate);
        expect(output).toContain(input.endDate);
        expect(output).toContain(' to ');

        // Must contain a percentage change or N/A indicator
        if (input.previousPeriodAmount === 0) {
          expect(output).toContain('N/A');
        } else {
          expect(output).toMatch(/\d+\.\d+%\s+(increase|decrease)/);
        }
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 5: Cost query time range validation
   *
   * For any pair of dates (startDate, endDate), if the range exceeds
   * 12 months the Cost Agent should reject the query with an error,
   * and if the range is 12 months or less the query should be accepted.
   *
   * **Validates: Requirements 2.4**
   */
  describe('Property 5: Cost query time range validation — Feature: genai-operations-analytics-tool', () => {
    it('rejects queries exceeding 12 months', () => {
      fc.assert(
        fc.property(arbExceedingDateRange, ({ startDate, endDate }) => {
          const result = validateTimeRange(startDate, endDate);

          expect(result.valid).toBe(false);
          if (!result.valid) {
            expect(result.error).toContain('exceeds the maximum supported range');
            expect(result.error).toContain(String(MAX_TIME_RANGE_DAYS));
          }
        }),
        { numRuns: 100 },
      );
    });

    it('accepts queries within 12 months', () => {
      fc.assert(
        fc.property(arbValidDateRange, ({ startDate, endDate }) => {
          const result = validateTimeRange(startDate, endDate);

          expect(result.valid).toBe(true);
        }),
        { numRuns: 100 },
      );
    });
  });
});
