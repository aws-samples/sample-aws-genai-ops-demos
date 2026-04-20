import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';

const PROJECT_ROOT = path.resolve(__dirname, '../..');

/**
 * Deployment logic extracted from deploy-all.ps1 / deploy-all.sh for testability.
 *
 * These are pure-logic functions that mirror the deployment scripts' behaviour
 * so we can validate parameter validation, stack naming, and module mapping
 * without executing the actual scripts.
 */

// Valid deployment modes (from PowerShell ValidateSet and Bash case statement)
const VALID_DEPLOYMENT_MODES = [
  'full',
  'cost',
  'health',
  'support',
  'trusted-advisor',
  'cur',
] as const;

type DeploymentMode = (typeof VALID_DEPLOYMENT_MODES)[number];

/** Returns true when the mode string is a recognised deployment mode. */
function isValidDeploymentMode(mode: string): mode is DeploymentMode {
  return (VALID_DEPLOYMENT_MODES as readonly string[]).includes(mode);
}

/** Maps a deployment mode to the set of module names that should be deployed. */
function getModulesForMode(mode: DeploymentMode): string[] {
  switch (mode) {
    case 'full':
      return ['Cost', 'Health', 'Support', 'TA', 'CUR'];
    case 'cost':
      return ['Cost'];
    case 'health':
      return ['Health'];
    case 'support':
      return ['Support'];
    case 'trusted-advisor':
      return ['TA'];
    case 'cur':
      return ['CUR'];
  }
}

/**
 * Generates the ordered list of stack names that would be deployed for a given
 * mode and region.  Mirrors the deploy-all scripts' deployment order:
 *   1. Core stacks (always)
 *   2. InfraStacks per module
 *   3. RuntimeStacks per module
 *   4. Orchestration stacks (full mode only)
 *   5. Frontend stack (always)
 */
function getStackNames(mode: DeploymentMode, region: string): string[] {
  const modules = getModulesForMode(mode);
  const stacks: string[] = [];

  // 1. Core stacks
  stacks.push(`GOATAuth-${region}`);
  stacks.push(`GOATData-${region}`);

  // 2. Infra stacks per module
  for (const mod of modules) {
    stacks.push(`GOAT${mod}Infra-${region}`);
  }

  // 3. Runtime stacks per module
  for (const mod of modules) {
    stacks.push(`GOAT${mod}Runtime-${region}`);
  }

  // 4. Orchestration stacks (full mode only)
  if (mode === 'full') {
    stacks.push(`GOATOrchInfra-${region}`);
    stacks.push(`GOATOrchRuntime-${region}`);
  }

  // 5. Frontend
  stacks.push(`GOATFrontend-${region}`);

  return stacks;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Deployment Scripts Unit Tests', () => {
  // -------------------------------------------------------------------------
  // 1. Deployment mode parameter validation
  // Validates: Requirement 11.5
  // -------------------------------------------------------------------------
  describe('Deployment mode validation', () => {
    it('should accept all six valid deployment modes', () => {
      for (const mode of VALID_DEPLOYMENT_MODES) {
        expect(isValidDeploymentMode(mode)).toBe(true);
      }
    });

    it('should reject invalid deployment modes', () => {
      const invalidModes = [
        'Full',        // wrong case
        'FULL',
        'partial',
        'all',
        'none',
        '',
        'cost-explorer',
        'health-dashboard',
        'ta',
        'trusted_advisor',
      ];
      for (const mode of invalidModes) {
        expect(isValidDeploymentMode(mode)).toBe(false);
      }
    });

    it('PowerShell script should declare ValidateSet with all valid modes', () => {
      const ps1 = fs.readFileSync(
        path.join(PROJECT_ROOT, 'deploy-all.ps1'),
        'utf-8',
      );
      for (const mode of VALID_DEPLOYMENT_MODES) {
        expect(ps1).toContain(`"${mode}"`);
      }
      expect(ps1).toContain('ValidateSet');
    });

    it('Bash script should validate deployment mode in a case statement', () => {
      const sh = fs.readFileSync(
        path.join(PROJECT_ROOT, 'deploy-all.sh'),
        'utf-8',
      );
      for (const mode of VALID_DEPLOYMENT_MODES) {
        expect(sh).toContain(mode);
      }
      // Should have an "Invalid deployment mode" error path
      expect(sh).toContain('Invalid deployment mode');
    });
  });

  // -------------------------------------------------------------------------
  // 2. Stack name generation with region suffix
  // Validates: Requirement 11.5
  // -------------------------------------------------------------------------
  describe('Stack name generation with region suffix', () => {
    const testRegion = 'us-west-2';

    it('all stack names should end with the region suffix', () => {
      for (const mode of VALID_DEPLOYMENT_MODES) {
        const stacks = getStackNames(mode, testRegion);
        for (const name of stacks) {
          expect(name).toMatch(new RegExp(`-${testRegion}$`));
        }
      }
    });

    it('core stacks should always be present regardless of mode', () => {
      for (const mode of VALID_DEPLOYMENT_MODES) {
        const stacks = getStackNames(mode, testRegion);
        expect(stacks).toContain(`GOATAuth-${testRegion}`);
        expect(stacks).toContain(`GOATData-${testRegion}`);
        expect(stacks).toContain(`GOATFrontend-${testRegion}`);
      }
    });

    it('full mode should include orchestration stacks', () => {
      const stacks = getStackNames('full', testRegion);
      expect(stacks).toContain(`GOATOrchInfra-${testRegion}`);
      expect(stacks).toContain(`GOATOrchRuntime-${testRegion}`);
    });

    it('single-module modes should NOT include orchestration stacks', () => {
      const singleModes: DeploymentMode[] = [
        'cost',
        'health',
        'support',
        'trusted-advisor',
        'cur',
      ];
      for (const mode of singleModes) {
        const stacks = getStackNames(mode, testRegion);
        expect(stacks).not.toContain(`GOATOrchInfra-${testRegion}`);
        expect(stacks).not.toContain(`GOATOrchRuntime-${testRegion}`);
      }
    });

    it('should produce unique stack names across different regions', () => {
      const stacksWest = getStackNames('full', 'us-west-2');
      const stacksEast = getStackNames('full', 'us-east-1');
      // No overlap between the two sets
      for (const name of stacksWest) {
        expect(stacksEast).not.toContain(name);
      }
    });

    it('CDK app.ts should use region-suffixed stack IDs matching the pattern', () => {
      const appTs = fs.readFileSync(
        path.join(PROJECT_ROOT, 'infrastructure/cdk/bin/app.ts'),
        'utf-8',
      );
      // Verify the GOAT prefix + region template literal pattern
      const expectedPatterns = [
        'GOATAuth-${region}',
        'GOATData-${region}',
        'GOATCostInfra-${region}',
        'GOATHealthInfra-${region}',
        'GOATSupportInfra-${region}',
        'GOATTAInfra-${region}',
        'GOATCURInfra-${region}',
        'GOATOrchInfra-${region}',
        'GOATCostRuntime-${region}',
        'GOATHealthRuntime-${region}',
        'GOATSupportRuntime-${region}',
        'GOATTARuntime-${region}',
        'GOATCURRuntime-${region}',
        'GOATOrchRuntime-${region}',
        'GOATFrontend-${region}',
      ];
      for (const pattern of expectedPatterns) {
        expect(appTs).toContain(pattern);
      }
    });
  });

  // -------------------------------------------------------------------------
  // 3. Module mapping per deployment mode
  // Validates: Requirement 11.5
  // -------------------------------------------------------------------------
  describe('Module mapping', () => {
    it('full mode should deploy all five modules', () => {
      const modules = getModulesForMode('full');
      expect(modules).toEqual(['Cost', 'Health', 'Support', 'TA', 'CUR']);
    });

    it('cost mode should deploy only Cost module', () => {
      expect(getModulesForMode('cost')).toEqual(['Cost']);
    });

    it('health mode should deploy only Health module', () => {
      expect(getModulesForMode('health')).toEqual(['Health']);
    });

    it('support mode should deploy only Support module', () => {
      expect(getModulesForMode('support')).toEqual(['Support']);
    });

    it('trusted-advisor mode should deploy only TA module', () => {
      expect(getModulesForMode('trusted-advisor')).toEqual(['TA']);
    });

    it('cur mode should deploy only CUR module', () => {
      expect(getModulesForMode('cur')).toEqual(['CUR']);
    });

    it('each single-module mode should produce exactly one module', () => {
      const singleModes: DeploymentMode[] = [
        'cost',
        'health',
        'support',
        'trusted-advisor',
        'cur',
      ];
      for (const mode of singleModes) {
        expect(getModulesForMode(mode)).toHaveLength(1);
      }
    });

    it('PowerShell script module mapping should match expected modules', () => {
      const ps1 = fs.readFileSync(
        path.join(PROJECT_ROOT, 'deploy-all.ps1'),
        'utf-8',
      );
      // Verify the switch statement maps correctly
      expect(ps1).toContain('"Cost", "Health", "Support", "TA", "CUR"');
      expect(ps1).toContain('"Cost"');
      expect(ps1).toContain('"Health"');
      expect(ps1).toContain('"Support"');
      expect(ps1).toContain('"TA"');
      expect(ps1).toContain('"CUR"');
    });

    it('Bash script module mapping should match expected modules', () => {
      const sh = fs.readFileSync(
        path.join(PROJECT_ROOT, 'deploy-all.sh'),
        'utf-8',
      );
      expect(sh).toContain('"Cost" "Health" "Support" "TA" "CUR"');
      expect(sh).toContain('"Cost"');
      expect(sh).toContain('"Health"');
      expect(sh).toContain('"Support"');
      expect(sh).toContain('"TA"');
      expect(sh).toContain('"CUR"');
    });
  });
});
