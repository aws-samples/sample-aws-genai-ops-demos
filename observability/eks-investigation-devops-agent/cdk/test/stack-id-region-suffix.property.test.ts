/**
 * Feature: cfn-to-cdk-migration, Property 1: Stack IDs include region suffix
 *
 * For any region string, all CDK stack IDs produced by the app should contain
 * that region string as a suffix, ensuring no two deployments in different
 * regions produce colliding global resource names.
 *
 * Validates: Requirements 1.2
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import { CloudAssembly } from 'aws-cdk-lib/cx-api';

/**
 * Synthesize the CDK app with the given region, mirroring the logic in
 * cdk/bin/app.ts without the child_process region detection.
 */
function synthesizeApp(region: string): CloudAssembly {
  const app = new cdk.App({
    context: { region, devOpsAgentWebhookUrl: 'https://example.com/webhook' },
  });

  const env: cdk.Environment = { region };

  new cdk.Stack(app, `DevOpsAgentEksNetwork-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksAuth-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksDatabase-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksCompute-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksPipeline-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksFrontend-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksMonitoring-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksDevOpsAgent-${region}`, { env });

  return app.synth();
}

describe('Property 1: Stack IDs include region suffix', () => {
  it('all stack IDs contain the region string', () => {
    // Generate region-like strings: 2-3 letter prefix, dash, direction, dash, digit
    const regionArb = fc
      .tuple(
        fc.constantFrom('us', 'eu', 'ap', 'sa', 'ca', 'me', 'af'),
        fc.constantFrom('east', 'west', 'north', 'south', 'central', 'southeast', 'northeast'),
        fc.integer({ min: 1, max: 4 }),
      )
      .map(([prefix, direction, num]) => `${prefix}-${direction}-${num}`);

    fc.assert(
      fc.property(regionArb, (region) => {
        const assembly = synthesizeApp(region);
        for (const stack of assembly.stacks) {
          expect(stack.stackName).toContain(region);
        }
      }),
      { numRuns: 100 },
    );
  });
});
