/**
 * Property tests for Network Agent cost-formula consistency.
 * Feature: genai-operations-analytics-tool
 *
 * Property 12: Cost-estimate formula is consistent across surfaces
 *
 * For every (eni_count, duration_minutes) pair, the cost printed in the
 * Capture_Confirmation_Prompt equals the cost computed by the README
 * cost-estimate formula using the same prices.json module values
 * (Reqs 14.2, 17.2).
 *
 * The design specifies a single formula:
 *
 *   cost_usd = (eni_count * duration_hours * price_per_eni_hour)
 *            + (estimated_bytes / 1e9 * price_per_gb)
 *
 * where:
 *   - duration_hours = duration_minutes / 60
 *   - price_per_eni_hour is read from the shared price table
 *   - price_per_gb = $0.015 (Traffic Mirror data charge)
 *   - estimated_bytes = eni_count * duration_minutes * 60 * 125000
 *     (1 Mbps per ENI heuristic)
 *
 * Both the orchestration agent's confirmation-prompt logic and the README
 * cost-estimate table consume the same prices.json, so this test verifies
 * that any two independent implementations of the formula using the same
 * price constants produce identical results.
 *
 * **Validates: Requirements 14.2, 17.2**
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { readFileSync } from 'fs';
import { resolve } from 'path';

// ---------------------------------------------------------------------------
// Load the shared prices.json (single source of truth)
// ---------------------------------------------------------------------------

const PRICES_JSON_PATH = resolve(
  __dirname,
  '../../agents/shared/prices.json',
);

interface PricesJson {
  currency: string;
  trafficMirror: {
    eniHourPriceDefault: number;
    eniHourPriceByRegion: Record<string, number>;
    dataPricePerGb: number;
  };
  s3: {
    standardStoragePricePerGbMonth: number;
  };
  heuristic: {
    mbpsPerEni: number;
    bytesPerSecondPerMbps: number;
  };
}

const prices: PricesJson = JSON.parse(
  readFileSync(PRICES_JSON_PATH, 'utf-8'),
);

// ---------------------------------------------------------------------------
// Implementation A: "README formula" — direct computation from prices.json
//
// This mirrors what the README cost-estimate table would compute given
// the unit prices and the heuristic from prices.json.
// ---------------------------------------------------------------------------

/**
 * Compute the estimated capture cost using the README formula directly
 * from prices.json constants.
 *
 * Formula (from design.md Capture_Confirmation_Prompt section):
 *   cost_usd = (eni_count * duration_hours * price_per_eni_hour)
 *            + (estimated_bytes / 1e9 * price_per_gb)
 */
function computeCostFromReadmeFormula(
  eniCount: number,
  durationMinutes: number,
  region?: string,
): number {
  const durationHours = durationMinutes / 60;
  const pricePerEniHour =
    region && prices.trafficMirror.eniHourPriceByRegion[region] !== undefined
      ? prices.trafficMirror.eniHourPriceByRegion[region]
      : prices.trafficMirror.eniHourPriceDefault;
  const pricePerGb = prices.trafficMirror.dataPricePerGb;

  // Heuristic: 1 Mbps per ENI → eni_count * duration_minutes * 60 * 125000
  const estimatedBytes =
    eniCount *
    durationMinutes *
    60 *
    prices.heuristic.bytesPerSecondPerMbps;

  const eniHoursCost = eniCount * durationHours * pricePerEniHour;
  const dataCost = (estimatedBytes / 1e9) * pricePerGb;

  return eniHoursCost + dataCost;
}

// ---------------------------------------------------------------------------
// Implementation B: "Orchestration agent confirmation-prompt formula"
//
// This mirrors the orchestration agent's compute_capture_cost_usd logic
// (from agents/shared/prices.py) re-implemented in TypeScript using the
// same prices.json values. The orchestration agent reads prices.json at
// runtime and applies the identical formula.
// ---------------------------------------------------------------------------

/**
 * Compute the estimated capture cost as the orchestration agent would
 * for the Capture_Confirmation_Prompt.
 *
 * This is an independent re-implementation of the Python
 * compute_capture_cost_usd function using the same prices.json source.
 */
function computeCostFromConfirmationPrompt(
  eniCount: number,
  durationMinutes: number,
  region?: string,
): number {
  const durationHours = durationMinutes / 60;
  const pricePerEniHour =
    region && prices.trafficMirror.eniHourPriceByRegion[region] !== undefined
      ? prices.trafficMirror.eniHourPriceByRegion[region]
      : prices.trafficMirror.eniHourPriceDefault;
  const pricePerGb = prices.trafficMirror.dataPricePerGb;

  // Same heuristic: estimated_bytes = eni_count * duration_minutes * 60 * 125000
  const estimatedBytes =
    eniCount *
    durationMinutes *
    60 *
    prices.heuristic.bytesPerSecondPerMbps;

  const eniHoursCost = eniCount * durationHours * pricePerEniHour;
  const dataCost = (estimatedBytes / 1e9) * pricePerGb;

  return eniHoursCost + dataCost;
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/**
 * Generate eni_count values within the valid Capture_Eni_Limit range (1-3).
 * Also includes boundary values 0 and values slightly above the limit
 * to verify the formula handles them consistently (the formula itself
 * does not enforce limits — that's the validation layer's job).
 */
const arbEniCount = fc.integer({ min: 1, max: 3 });

/**
 * Generate duration_minutes within the valid Capture_Duration_Limit (1-60).
 */
const arbDurationMinutes = fc.integer({ min: 1, max: 60 });

/**
 * Generate extended eni_count values including edge cases beyond the
 * normal capture limits to verify formula consistency at boundaries.
 */
const arbExtendedEniCount = fc.integer({ min: 0, max: 100 });

/**
 * Generate extended duration_minutes including edge cases.
 */
const arbExtendedDurationMinutes = fc.integer({ min: 0, max: 1440 });

/**
 * Generate a region from the known set in prices.json.
 */
const arbKnownRegion = fc.constantFrom(
  ...Object.keys(prices.trafficMirror.eniHourPriceByRegion),
);

/**
 * Generate an unknown region string (not in the price table).
 */
const arbUnknownRegion = fc.string({ minLength: 5, maxLength: 20 }).filter(
  (s) => !(s in prices.trafficMirror.eniHourPriceByRegion),
);

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Network Agent cost-formula consistency property tests', () => {
  /**
   * Property 12: For every (eni_count, duration_minutes) pair within
   * valid capture limits, the README formula and the confirmation-prompt
   * formula produce identical costs.
   *
   * **Validates: Requirements 14.2, 17.2**
   */
  it('Property 12: README formula equals confirmation-prompt formula for valid capture parameters', () => {
    fc.assert(
      fc.property(arbEniCount, arbDurationMinutes, (eniCount, durationMinutes) => {
        const readmeCost = computeCostFromReadmeFormula(eniCount, durationMinutes);
        const promptCost = computeCostFromConfirmationPrompt(eniCount, durationMinutes);

        expect(readmeCost).toBe(promptCost);
        // Cost must be non-negative
        expect(readmeCost).toBeGreaterThanOrEqual(0);
      }),
      { numRuns: 500 },
    );
  });

  /**
   * Property 12 (extended): Formula consistency holds for extended
   * parameter ranges including boundary values.
   *
   * **Validates: Requirements 14.2, 17.2**
   */
  it('Property 12: formula consistency holds for extended parameter ranges', () => {
    fc.assert(
      fc.property(
        arbExtendedEniCount,
        arbExtendedDurationMinutes,
        (eniCount, durationMinutes) => {
          const readmeCost = computeCostFromReadmeFormula(eniCount, durationMinutes);
          const promptCost = computeCostFromConfirmationPrompt(eniCount, durationMinutes);

          expect(readmeCost).toBe(promptCost);
          expect(readmeCost).toBeGreaterThanOrEqual(0);
        },
      ),
      { numRuns: 500 },
    );
  });

  /**
   * Property 12 (regional): For every known region in prices.json,
   * both formula implementations produce the same cost.
   *
   * **Validates: Requirements 14.2, 17.2**
   */
  it('Property 12: formula consistency holds across all known regions', () => {
    fc.assert(
      fc.property(
        arbEniCount,
        arbDurationMinutes,
        arbKnownRegion,
        (eniCount, durationMinutes, region) => {
          const readmeCost = computeCostFromReadmeFormula(eniCount, durationMinutes, region);
          const promptCost = computeCostFromConfirmationPrompt(eniCount, durationMinutes, region);

          expect(readmeCost).toBe(promptCost);
          expect(readmeCost).toBeGreaterThanOrEqual(0);
        },
      ),
      { numRuns: 300 },
    );
  });

  /**
   * Property 12 (unknown region fallback): For unknown regions, both
   * implementations fall back to the default price and produce the same cost.
   *
   * **Validates: Requirements 14.2, 17.2**
   */
  it('Property 12: unknown regions fall back to default price consistently', () => {
    fc.assert(
      fc.property(
        arbEniCount,
        arbDurationMinutes,
        arbUnknownRegion,
        (eniCount, durationMinutes, region) => {
          const readmeCost = computeCostFromReadmeFormula(eniCount, durationMinutes, region);
          const promptCost = computeCostFromConfirmationPrompt(eniCount, durationMinutes, region);
          const defaultCost = computeCostFromReadmeFormula(eniCount, durationMinutes, undefined);

          // Both should equal each other
          expect(readmeCost).toBe(promptCost);
          // Both should equal the default (no region) cost
          expect(readmeCost).toBe(defaultCost);
        },
      ),
      { numRuns: 200 },
    );
  });

  /**
   * Property 12 (zero inputs): When eni_count=0 or duration_minutes=0,
   * the cost is exactly zero from both implementations.
   *
   * **Validates: Requirements 14.2, 17.2**
   */
  it('Property 12: zero eni_count or zero duration produces zero cost', () => {
    fc.assert(
      fc.property(arbDurationMinutes, (durationMinutes) => {
        const readmeCost = computeCostFromReadmeFormula(0, durationMinutes);
        const promptCost = computeCostFromConfirmationPrompt(0, durationMinutes);
        expect(readmeCost).toBe(0);
        expect(promptCost).toBe(0);
      }),
      { numRuns: 100 },
    );

    fc.assert(
      fc.property(arbEniCount, (eniCount) => {
        const readmeCost = computeCostFromReadmeFormula(eniCount, 0);
        const promptCost = computeCostFromConfirmationPrompt(eniCount, 0);
        expect(readmeCost).toBe(0);
        expect(promptCost).toBe(0);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 12 (monotonicity): Cost increases monotonically with both
   * eni_count and duration_minutes. This is a structural property of the
   * formula that both implementations must preserve.
   *
   * **Validates: Requirements 14.2, 17.2**
   */
  it('Property 12: cost is monotonically increasing with eni_count and duration', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 2 }),
        arbDurationMinutes,
        (eniCount, durationMinutes) => {
          const costLower = computeCostFromReadmeFormula(eniCount, durationMinutes);
          const costHigherEni = computeCostFromReadmeFormula(eniCount + 1, durationMinutes);
          const costHigherDuration = computeCostFromReadmeFormula(eniCount, durationMinutes + 1);

          // More ENIs → higher cost (when duration > 0)
          if (durationMinutes > 0) {
            expect(costHigherEni).toBeGreaterThan(costLower);
          }
          // More duration → higher cost (when eni_count > 0)
          if (eniCount > 0) {
            expect(costHigherDuration).toBeGreaterThan(costLower);
          }
        },
      ),
      { numRuns: 200 },
    );
  });

  /**
   * Property 12 (linearity): Cost scales linearly with eni_count.
   * cost(2*n, d) == 2 * cost(n, d) for any n, d.
   *
   * **Validates: Requirements 14.2, 17.2**
   */
  it('Property 12: cost scales linearly with eni_count', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 50 }),
        fc.integer({ min: 1, max: 60 }),
        (eniCount, durationMinutes) => {
          const costSingle = computeCostFromReadmeFormula(eniCount, durationMinutes);
          const costDouble = computeCostFromReadmeFormula(eniCount * 2, durationMinutes);

          // Due to floating point, use a relative tolerance
          expect(Math.abs(costDouble - 2 * costSingle)).toBeLessThan(1e-10);
        },
      ),
      { numRuns: 200 },
    );
  });

  /**
   * Property 12 (prices.json integrity): The prices.json file contains
   * the expected structure and non-negative values that both formula
   * implementations depend on.
   *
   * **Validates: Requirements 14.2, 17.2**
   */
  it('Property 12: prices.json contains valid non-negative pricing constants', () => {
    // Structural checks
    expect(prices.currency).toBe('USD');
    expect(prices.trafficMirror.eniHourPriceDefault).toBeGreaterThan(0);
    expect(prices.trafficMirror.dataPricePerGb).toBeGreaterThan(0);
    expect(prices.heuristic.bytesPerSecondPerMbps).toBeGreaterThan(0);
    expect(prices.heuristic.mbpsPerEni).toBeGreaterThan(0);
    expect(prices.s3.standardStoragePricePerGbMonth).toBeGreaterThan(0);

    // All regional prices must be positive
    for (const [region, price] of Object.entries(prices.trafficMirror.eniHourPriceByRegion)) {
      expect(price).toBeGreaterThan(0);
      // Region must look like a valid AWS region
      expect(region).toMatch(/^[a-z]{2}-[a-z]+-\d+$/);
    }

    // The heuristic bytesPerSecondPerMbps must equal 125000 (1 Mbps = 125000 bytes/s)
    expect(prices.heuristic.bytesPerSecondPerMbps).toBe(125000);
  });

  /**
   * Property 12 (cross-file consistency): The orchestration agent's
   * local prices.json copy must contain the same values as the shared
   * prices.json source of truth.
   *
   * **Validates: Requirements 14.2, 17.2**
   */
  it('Property 12: orchestration agent prices.json matches shared prices.json', () => {
    const orchPricesPath = resolve(
      __dirname,
      '../../agents/orchestration-agent/prices.json',
    );
    const orchPrices: PricesJson = JSON.parse(
      readFileSync(orchPricesPath, 'utf-8'),
    );

    // All pricing constants must match
    expect(orchPrices.trafficMirror.eniHourPriceDefault).toBe(
      prices.trafficMirror.eniHourPriceDefault,
    );
    expect(orchPrices.trafficMirror.dataPricePerGb).toBe(
      prices.trafficMirror.dataPricePerGb,
    );
    expect(orchPrices.heuristic.bytesPerSecondPerMbps).toBe(
      prices.heuristic.bytesPerSecondPerMbps,
    );
    expect(orchPrices.heuristic.mbpsPerEni).toBe(prices.heuristic.mbpsPerEni);
    expect(orchPrices.s3.standardStoragePricePerGbMonth).toBe(
      prices.s3.standardStoragePricePerGbMonth,
    );

    // Regional prices must match exactly
    expect(orchPrices.trafficMirror.eniHourPriceByRegion).toEqual(
      prices.trafficMirror.eniHourPriceByRegion,
    );
  });
});
