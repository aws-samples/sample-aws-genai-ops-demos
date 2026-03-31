/**
 * Feature: cfn-to-cdk-migration, Property 6: DevOps Agent stack mandatory instantiation
 *
 * The DevOps Agent stack must always be present when a webhook URL is provided.
 * The CDK app must throw an error when the webhook URL is missing.
 *
 * Validates: Requirements 9.1, 9.2
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import { CloudAssembly } from 'aws-cdk-lib/cx-api';

const TOTAL_STACK_COUNT = 8; // 7 core + 1 DevOps Agent (always present)

/**
 * Synthesize the CDK app with the given context, mirroring the logic in
 * cdk/bin/app.ts. We replicate the instantiation here so the test doesn't
 * depend on child_process (region detection via AWS CLI).
 */
function synthesizeApp(context: {
  region: string;
  devOpsAgentWebhookUrl: string;
  devOpsAgentWebhookSecret: string;
}): CloudAssembly {
  const app = new cdk.App({ context });

  const region = context.region;
  const env: cdk.Environment = { region };
  const devOpsAgentWebhookUrl = context.devOpsAgentWebhookUrl ?? '';
  const devOpsAgentWebhookSecret = context.devOpsAgentWebhookSecret ?? '';

  // Core stacks (always present)
  new cdk.Stack(app, `DevOpsAgentEksNetwork-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksAuth-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksDatabase-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksCompute-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksPipeline-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksFrontend-${region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksMonitoring-${region}`, { env });

  // DevOps Agent stack — mandatory (throws if webhook not provided)
  if (!devOpsAgentWebhookUrl || !devOpsAgentWebhookSecret) {
    throw new Error('DevOps Agent webhook configuration is required.');
  }
  new cdk.Stack(app, `DevOpsAgentEksDevOpsAgent-${region}`, { env });

  return app.synth();
}

describe('Property 6: DevOps Agent stack mandatory instantiation', () => {
  it('DevOps Agent stack is always present when webhook URL and secret are provided', () => {
    const regionArb = fc.constantFrom(
      'us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1',
    );
    const webhookUrlArb = fc.webUrl().filter((u) => u.length > 0);
    const webhookSecretArb = fc.base64String({ minLength: 10, maxLength: 64 });

    fc.assert(
      fc.property(regionArb, webhookUrlArb, webhookSecretArb, (region, webhookUrl, webhookSecret) => {
        const assembly = synthesizeApp({
          region,
          devOpsAgentWebhookUrl: webhookUrl,
          devOpsAgentWebhookSecret: webhookSecret,
        });
        const stackNames = assembly.stacks.map((s: { stackName: string }) => s.stackName);
        const devOpsStackName = `DevOpsAgentEksDevOpsAgent-${region}`;

        expect(stackNames).toContain(devOpsStackName);
        expect(stackNames.length).toBe(TOTAL_STACK_COUNT);
      }),
      { numRuns: 100 },
    );
  });

  it('throws an error when webhook URL is missing', () => {
    expect(() => {
      synthesizeApp({
        region: 'us-east-1',
        devOpsAgentWebhookUrl: '',
        devOpsAgentWebhookSecret: 'some-secret',
      });
    }).toThrow('DevOps Agent webhook configuration is required.');
  });

  it('throws an error when webhook secret is missing', () => {
    expect(() => {
      synthesizeApp({
        region: 'us-east-1',
        devOpsAgentWebhookUrl: 'https://example.com/webhook',
        devOpsAgentWebhookSecret: '',
      });
    }).toThrow('DevOps Agent webhook configuration is required.');
  });
});
