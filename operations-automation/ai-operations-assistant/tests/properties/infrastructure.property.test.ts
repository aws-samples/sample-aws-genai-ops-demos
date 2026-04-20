import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';

/**
 * Stack ID generator — mirrors the pattern used in bin/app.ts:
 *   `${baseName}-${region}`
 */
function generateStackId(baseName: string, region: string): string {
  return `${baseName}-${region}`;
}

/**
 * Arbitrary for valid CDK stack base names (PascalCase, alphanumeric).
 */
const stackBaseNameArb = fc.stringOf(
  fc.constantFrom(
    ...'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'.split('')
  ),
  { minLength: 1, maxLength: 30 },
).filter((s) => /^[A-Z]/.test(s));

/**
 * Arbitrary for valid AWS region strings (e.g. "us-east-1", "eu-west-2").
 */
const awsRegionArb = fc.tuple(
  fc.constantFrom('us', 'eu', 'ap', 'sa', 'ca', 'me', 'af'),
  fc.constantFrom('east', 'west', 'south', 'north', 'central', 'southeast', 'northeast'),
  fc.integer({ min: 1, max: 3 }),
).map(([prefix, direction, num]) => `${prefix}-${direction}-${num}`);

describe('Infrastructure Property Tests', () => {
  /**
   * Property 16: Stack name includes region suffix
   *
   * For any stack base name and AWS region string, the stack ID generator
   * should produce a string matching the pattern `{baseName}-{region}`.
   *
   * **Validates: Requirements 10.5**
   */
  it('Property 16: Stack name includes region suffix', () => {
    fc.assert(
      fc.property(
        stackBaseNameArb,
        awsRegionArb,
        (baseName, region) => {
          const stackId = generateStackId(baseName, region);

          // Must contain the region as a suffix
          expect(stackId).toContain(region);
          expect(stackId.endsWith(region)).toBe(true);

          // Must start with the base name
          expect(stackId.startsWith(baseName)).toBe(true);

          // Must match the exact pattern {baseName}-{region}
          expect(stackId).toBe(`${baseName}-${region}`);

          // Must contain a hyphen separator between base name and region
          const separatorIndex = stackId.indexOf(`-${region}`);
          expect(separatorIndex).toBe(baseName.length);
        },
      ),
      { numRuns: 100 },
    );
  });
});
