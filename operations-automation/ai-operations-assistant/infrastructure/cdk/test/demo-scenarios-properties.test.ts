import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { DemoScenarioAccountHealthStack } from '../lib/demo-scenario-account-health-stack';
import { DemoScenarioTlsFragmentationStack } from '../lib/demo-scenario-tls-fragmentation-stack';

/**
 * Property-based tests for G.O.A.T. Demo Scenarios CDK stacks.
 *
 * These tests validate universal correctness properties across generated inputs
 * using fast-check for property-based testing.
 */
describe('Feature: goat-demo-scenarios', () => {
  // =========================================================================
  // Property 2: Stack Naming Convention
  // =========================================================================
  // **Validates: Requirements 2.1, 2.3**

  describe('Property 2: Stack Naming Convention', () => {
    test('Property 2: Stack IDs match GOATDemoScenario<Name>-${region} pattern', () => {
      // Generate region-like strings: lowercase alphanumeric with hyphens, 5-20 chars
      const regionArb = fc
        .string({ minLength: 5, maxLength: 20, unit: fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz0123456789-'.split('')) })
        .filter((s: string) => !s.startsWith('-') && !s.endsWith('-') && !s.includes('--'));

      fc.assert(
        fc.property(
          regionArb,
          (region) => {
            const app = new cdk.App();

            // Instantiate Scenario A stack with region suffix
            const stackA = new DemoScenarioAccountHealthStack(
              app,
              `GOATDemoScenarioA-${region}`,
              { env: { account: '123456789012', region: 'us-east-1' } }
            );

            // Instantiate Scenario C stack with region suffix
            const stackC = new DemoScenarioTlsFragmentationStack(
              app,
              `GOATDemoScenarioTLS-${region}`,
              { env: { account: '123456789012', region: 'us-east-1' } }
            );

            // Verify Scenario A stack name matches pattern
            expect(stackA.stackName).toMatch(/^GOATDemoScenarioA-.+/);
            expect(stackA.stackName).toContain(region);

            // Verify Scenario C stack name matches pattern
            expect(stackC.stackName).toMatch(/^GOATDemoScenarioTLS-.+/);
            expect(stackC.stackName).toContain(region);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  // =========================================================================
  // Property 3: Resource Tagging Completeness
  // =========================================================================
  // **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6, 3.7**

  describe('Property 3: Resource Tagging Completeness', () => {
    /**
     * Helper: Extract all resources with a Tags property from a CloudFormation template.
     * Returns an array of { logicalId, resourceType, tags } objects.
     */
    function getTaggableResources(template: Template): Array<{
      logicalId: string;
      resourceType: string;
      tags: Array<{ Key: string; Value: string }>;
    }> {
      const templateJson = template.toJSON();
      const resources = templateJson.Resources || {};
      const result: Array<{
        logicalId: string;
        resourceType: string;
        tags: Array<{ Key: string; Value: string }>;
      }> = [];

      for (const [logicalId, resource] of Object.entries(resources)) {
        const res = resource as { Type: string; Properties?: Record<string, unknown> };
        const props = res.Properties || {};

        // Check for Tags property (standard CloudFormation tag format)
        if (props.Tags && Array.isArray(props.Tags)) {
          result.push({
            logicalId,
            resourceType: res.Type,
            tags: props.Tags as Array<{ Key: string; Value: string }>,
          });
        }
      }

      return result;
    }

    test('Property 3: Scenario A — all taggable resources have goat-demo=true, auto-delete=no, and goat-scenario=a', () => {
      // This property is deterministic but uses fc.assert for consistent test framing
      fc.assert(
        fc.property(fc.constant(null), () => {
          const app = new cdk.App();
          const stack = new DemoScenarioAccountHealthStack(app, 'TestStackA', {
            env: { account: '123456789012', region: 'us-east-1' },
          });

          const template = Template.fromStack(stack);
          const taggableResources = getTaggableResources(template);

          // Must have at least some taggable resources
          expect(taggableResources.length).toBeGreaterThan(0);

          for (const resource of taggableResources) {
            const tagMap = new Map(
              resource.tags.map((t) => [t.Key, t.Value])
            );

            // Verify goat-demo=true
            expect(tagMap.get('goat-demo')).toBe('true');

            // Verify auto-delete=no
            expect(tagMap.get('auto-delete')).toBe('no');

            // Verify goat-scenario=a
            expect(tagMap.get('goat-scenario')).toBe('a');
          }
        }),
        { numRuns: 100 }
      );
    });

    test('Property 3: Scenario C — all taggable resources have goat-demo=true, auto-delete=no, and goat-scenario=tls-fragmentation', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          const app = new cdk.App();
          const stack = new DemoScenarioTlsFragmentationStack(app, 'TestStackC', {
            env: { account: '123456789012', region: 'us-east-1' },
          });

          const template = Template.fromStack(stack);
          const taggableResources = getTaggableResources(template);

          // Must have at least some taggable resources
          expect(taggableResources.length).toBeGreaterThan(0);

          for (const resource of taggableResources) {
            const tagMap = new Map(
              resource.tags.map((t) => [t.Key, t.Value])
            );

            // Verify goat-demo=true
            expect(tagMap.get('goat-demo')).toBe('true');

            // Verify auto-delete=no
            expect(tagMap.get('auto-delete')).toBe('no');

            // Verify goat-scenario=tls-fragmentation
            expect(tagMap.get('goat-scenario')).toBe('tls-fragmentation');
          }
        }),
        { numRuns: 100 }
      );
    });
  });

  // =========================================================================
  // Property 4: Subnet CIDR Isolation
  // =========================================================================
  // **Validates: Requirements 11.4**

  describe('Property 4: Subnet CIDR Isolation', () => {
    /**
     * Parse a CIDR string (e.g., "10.99.1.0/24") into a numeric range [start, end].
     */
    function cidrToRange(cidr: string): { start: number; end: number } {
      const [ip, prefix] = cidr.split('/');
      const prefixLen = parseInt(prefix, 10);
      const parts = ip.split('.').map(Number);
      const ipNum =
        (parts[0] << 24) + (parts[1] << 16) + (parts[2] << 8) + parts[3];
      const mask = ~((1 << (32 - prefixLen)) - 1);
      const start = ipNum & mask;
      const end = start + ((1 << (32 - prefixLen)) - 1);
      return { start: start >>> 0, end: end >>> 0 };
    }

    /**
     * Check if two CIDR ranges overlap.
     */
    function cidrsOverlap(cidr1: string, cidr2: string): boolean {
      const range1 = cidrToRange(cidr1);
      const range2 = cidrToRange(cidr2);
      return range1.start <= range2.end && range2.start <= range1.end;
    }

    /**
     * Extract all subnet CIDR blocks from a CloudFormation template.
     */
    function getSubnetCidrs(template: Template): string[] {
      const templateJson = template.toJSON();
      const resources = templateJson.Resources || {};
      const cidrs: string[] = [];

      for (const [, resource] of Object.entries(resources)) {
        const res = resource as { Type: string; Properties?: Record<string, unknown> };
        if (
          res.Type === 'AWS::EC2::Subnet' &&
          res.Properties?.CidrBlock &&
          typeof res.Properties.CidrBlock === 'string'
        ) {
          cidrs.push(res.Properties.CidrBlock);
        }
      }

      return cidrs;
    }

    test('Property 4: Scenario A subnet CIDRs do not overlap with Scenario C subnet CIDRs', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          const app = new cdk.App();

          // Deploy both stacks together (shared VPC scenario)
          const stackA = new DemoScenarioAccountHealthStack(app, 'IsolationTestA', {
            env: { account: '123456789012', region: 'us-east-1' },
          });

          const stackC = new DemoScenarioTlsFragmentationStack(
            app,
            'IsolationTestC',
            {
              env: { account: '123456789012', region: 'us-east-1' },
              sharedVpc: stackA.vpc,
            }
          );

          const templateA = Template.fromStack(stackA);
          const templateC = Template.fromStack(stackC);

          const scenarioACidrs = getSubnetCidrs(templateA);
          const scenarioCCidrs = getSubnetCidrs(templateC);

          // Scenario A should have subnets in 10.99.1.0/24 – 10.99.9.0/24 range
          for (const cidrA of scenarioACidrs) {
            const rangeA = cidrToRange(cidrA);
            // Verify Scenario A CIDRs are in the expected range (10.99.1.0 – 10.99.9.255)
            const expectedRangeStart = cidrToRange('10.99.1.0/24').start;
            const expectedRangeEnd = cidrToRange('10.99.9.0/24').end;
            expect(rangeA.start).toBeGreaterThanOrEqual(expectedRangeStart);
            expect(rangeA.end).toBeLessThanOrEqual(expectedRangeEnd);
          }

          // Scenario C subnets in shared VPC should be in 10.99.10.0/24 – 10.99.20.0/24
          // or in the inspection VPC (10.98.x.x)
          for (const cidrC of scenarioCCidrs) {
            const rangeC = cidrToRange(cidrC);
            const inspectionStart = cidrToRange('10.98.0.0/16').start;
            const inspectionEnd = cidrToRange('10.98.0.0/16').end;
            const sharedVpcRangeStart = cidrToRange('10.99.10.0/24').start;
            const sharedVpcRangeEnd = cidrToRange('10.99.20.0/24').end;

            // Each Scenario C CIDR must be either in inspection VPC or in 10.99.10-20 range
            const inInspection =
              rangeC.start >= inspectionStart && rangeC.end <= inspectionEnd;
            const inSharedRange =
              rangeC.start >= sharedVpcRangeStart &&
              rangeC.end <= sharedVpcRangeEnd;

            expect(inInspection || inSharedRange).toBe(true);
          }

          // Critical check: No overlap between any Scenario A CIDR and Scenario C CIDR
          for (const cidrA of scenarioACidrs) {
            for (const cidrC of scenarioCCidrs) {
              expect(cidrsOverlap(cidrA, cidrC)).toBe(false);
            }
          }
        }),
        { numRuns: 100 }
      );
    });
  });
});
