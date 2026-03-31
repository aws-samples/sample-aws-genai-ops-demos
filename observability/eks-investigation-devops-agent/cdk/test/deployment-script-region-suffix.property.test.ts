/**
 * Feature: cfn-to-cdk-migration, Property 9: Deployment script region-suffixed stack names
 *
 * For any AWS region value, all `aws cloudformation describe-stacks --stack-name`
 * invocations in the deployment scripts should reference stack names that include
 * a region variable as a suffix.
 *
 * Validates: Requirements 10.3
 */
import * as fc from 'fast-check';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Extract all --stack-name values from `aws cloudformation describe-stacks`
 * invocations in a script file.
 */
function extractStackNames(content: string): string[] {
  // Match --stack-name followed by a quoted or unquoted value.
  // Handles multi-line continuations (backslash or backtick).
  // The value may be on the same line or the next line after a continuation.
  const pattern = /--stack-name\s+["']?([^"'\s\\`]+)["']?/g;
  const names: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(content)) !== null) {
    names.push(match[1]);
  }
  return names;
}

/** Check that a stack name contains a region variable reference. */
function hasRegionSuffix(stackName: string, type: 'bash' | 'powershell'): boolean {
  // Both scripts use $AWS_REGION (bash uses it directly, PowerShell also uses $AWS_REGION)
  return stackName.includes('$AWS_REGION');
}

describe('Property 9: Deployment script region-suffixed stack names', () => {
  const bashPath = path.resolve(__dirname, '../../deploy-all.sh');
  const psPath = path.resolve(__dirname, '../../deploy-all.ps1');

  const bashContent = fs.readFileSync(bashPath, 'utf-8');
  const psContent = fs.readFileSync(psPath, 'utf-8');

  const bashStackNames = extractStackNames(bashContent);
  const psStackNames = extractStackNames(psContent);

  it('deploy-all.sh has at least one describe-stacks invocation', () => {
    expect(bashStackNames.length).toBeGreaterThan(0);
  });

  it('deploy-all.ps1 has at least one describe-stacks invocation', () => {
    expect(psStackNames.length).toBeGreaterThan(0);
  });

  it('all bash --stack-name values contain $AWS_REGION suffix', () => {
    for (const name of bashStackNames) {
      expect(name).toContain('$AWS_REGION');
    }
  });

  it('all PowerShell --stack-name values contain $AWS_REGION suffix', () => {
    for (const name of psStackNames) {
      expect(name).toContain('$AWS_REGION');
    }
  });

  it('substituting any region into bash stack names produces valid region-suffixed names', () => {
    const regionArb = fc
      .tuple(
        fc.constantFrom('us', 'eu', 'ap', 'sa', 'ca', 'me', 'af'),
        fc.constantFrom('east', 'west', 'north', 'south', 'central', 'southeast', 'northeast'),
        fc.integer({ min: 1, max: 4 }),
      )
      .map(([prefix, direction, num]) => `${prefix}-${direction}-${num}`);

    fc.assert(
      fc.property(regionArb, (region) => {
        for (const name of bashStackNames) {
          const resolved = name.replace(/\$AWS_REGION/g, region);
          // Must end with the region string
          expect(resolved).toMatch(new RegExp(`-${region}$`));
          // Must not still contain variable references
          expect(resolved).not.toContain('$');
        }
      }),
      { numRuns: 100 },
    );
  });

  it('substituting any region into PowerShell stack names produces valid region-suffixed names', () => {
    const regionArb = fc
      .tuple(
        fc.constantFrom('us', 'eu', 'ap', 'sa', 'ca', 'me', 'af'),
        fc.constantFrom('east', 'west', 'north', 'south', 'central', 'southeast', 'northeast'),
        fc.integer({ min: 1, max: 4 }),
      )
      .map(([prefix, direction, num]) => `${prefix}-${direction}-${num}`);

    fc.assert(
      fc.property(regionArb, (region) => {
        for (const name of psStackNames) {
          const resolved = name.replace(/\$AWS_REGION/g, region);
          // Must end with the region string
          expect(resolved).toMatch(new RegExp(`-${region}$`));
          // Must not still contain variable references
          expect(resolved).not.toContain('$');
        }
      }),
      { numRuns: 100 },
    );
  });
});
