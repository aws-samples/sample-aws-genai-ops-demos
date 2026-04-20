/**
 * Property tests for Trusted Advisor Agent categorization logic.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise a pure TypeScript implementation that mirrors the
 * Python TA Agent's `_categorize_by_pillar` behaviour so it can be
 * verified with fast-check without calling boto3.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { arbTARecommendation, arbPillar } from '../generators/ta-recommendation.gen';

// ---------------------------------------------------------------------------
// Local TypeScript implementation mirroring the Python TA Agent logic
// ---------------------------------------------------------------------------

/** The five canonical Trusted Advisor pillars. */
const TA_PILLARS = [
  'cost_optimizing',
  'security',
  'performance',
  'fault_tolerance',
  'service_limits',
] as const;

type Pillar = (typeof TA_PILLARS)[number];

/** A recommendation object with at least a `pillar` field. */
interface TARecommendation {
  pillar: string;
  [key: string]: unknown;
}

/**
 * Categorize Trusted Advisor recommendations by pillar.
 *
 * Mirrors `_categorize_by_pillar` in `agents/ta-agent/main.py`.
 * Returns a dict mapping each pillar to its list of recommendations.
 * Recommendations whose pillar does not match a known pillar are placed
 * under an "other" key.
 */
function categorizeByPillar(
  recommendations: TARecommendation[],
): Record<string, TARecommendation[]> {
  const categorized: Record<string, TARecommendation[]> = {};
  for (const pillar of TA_PILLARS) {
    categorized[pillar] = [];
  }

  const uncategorized: TARecommendation[] = [];

  for (const rec of recommendations) {
    const normalized = rec.pillar.toLowerCase().replace(/ /g, '_');
    if (TA_PILLARS.includes(normalized as Pillar)) {
      categorized[normalized].push(rec);
    } else {
      uncategorized.push(rec);
    }
  }

  if (uncategorized.length > 0) {
    categorized['other'] = uncategorized;
  }

  return categorized;
}

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Trusted Advisor Agent property tests', () => {
  /**
   * Property 8: Trusted Advisor categorization by pillar
   *
   * For any set of Trusted Advisor recommendations with assigned pillars,
   * the categorization function should group all recommendations by their
   * pillar, and every recommendation should appear in exactly one pillar
   * group.
   *
   * **Validates: Requirements 5.3**
   */
  describe('Property 8: Trusted Advisor categorization by pillar — Feature: genai-operations-analytics-tool, Property 8: Trusted Advisor categorization by pillar', () => {
    it('every recommendation appears in exactly one pillar group', () => {
      fc.assert(
        fc.property(
          fc.array(arbTARecommendation, { minLength: 1, maxLength: 30 }),
          (recommendations) => {
            const categorized = categorizeByPillar(recommendations);

            // Collect all recommendations across all groups
            const allGrouped: TARecommendation[] = [];
            for (const group of Object.values(categorized)) {
              allGrouped.push(...group);
            }

            // Total count across all groups must equal input count
            expect(allGrouped.length).toBe(recommendations.length);

            // Each input recommendation must appear exactly once
            for (const rec of recommendations) {
              const occurrences = allGrouped.filter((r) => r === rec).length;
              expect(occurrences).toBe(1);
            }
          },
        ),
        { numRuns: 100 },
      );
    });

    it('all 5 pillars are represented as keys', () => {
      fc.assert(
        fc.property(
          fc.array(arbTARecommendation, { minLength: 0, maxLength: 30 }),
          (recommendations) => {
            const categorized = categorizeByPillar(recommendations);

            // All 5 canonical pillars must always be present as keys
            for (const pillar of TA_PILLARS) {
              expect(categorized).toHaveProperty(pillar);
              expect(Array.isArray(categorized[pillar])).toBe(true);
            }
          },
        ),
        { numRuns: 100 },
      );
    });

    it('total count across all groups equals input count', () => {
      fc.assert(
        fc.property(
          fc.array(arbTARecommendation, { minLength: 0, maxLength: 30 }),
          (recommendations) => {
            const categorized = categorizeByPillar(recommendations);

            const totalGrouped = Object.values(categorized).reduce(
              (sum, group) => sum + group.length,
              0,
            );

            expect(totalGrouped).toBe(recommendations.length);
          },
        ),
        { numRuns: 100 },
      );
    });
  });
});
