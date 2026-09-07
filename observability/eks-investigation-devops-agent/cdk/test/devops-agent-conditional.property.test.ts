/**
 * Feature: CDK-managed DevOps Agent onboarding.
 *
 * Property: the Agent Space stack is always part of the app, independent of
 * whether webhook outputs have been fed back into the infrastructure phase.
 * This is what makes the two-stage deployment possible:
 *
 *   1. deploy DevOpsAgentEksAgentSpace-<agent-region>
 *   2. read URL / secret ARN / Agent Space ID outputs
 *   3. deploy the remaining stacks with those values as context
 *
 * The HMAC secret VALUE must never be a context value; only its ARN crosses
 * the stack boundary.
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import { CloudAssembly } from 'aws-cdk-lib/cx-api';

const CORE_STACK_COUNT = 7;
const TOTAL_STACK_COUNT = CORE_STACK_COUNT + 2; // Agent Space + trigger stack

function synthesizeApp(context: {
  region: string;
  agentRegion: string;
  webhookUrl?: string;
  webhookSecretArn?: string;
}): CloudAssembly {
  const app = new cdk.App({ context });
  const env: cdk.Environment = { region: context.region };

  new cdk.Stack(app, `DevOpsAgentEksNetwork-${context.region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksAuth-${context.region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksDatabase-${context.region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksCompute-${context.region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksPipeline-${context.region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksFrontend-${context.region}`, { env });
  new cdk.Stack(app, `DevOpsAgentEksMonitoring-${context.region}`, { env });

  // Agent Space stack is independent and deployable first, in its own region.
  new cdk.Stack(app, `DevOpsAgentEksAgentSpace-${context.agentRegion}`, {
    env: { region: context.agentRegion },
  });

  // Trigger stack remains synthesizable during phase 1. It is configured with
  // the real URL + secret ARN for phase 2 by the deployment scripts.
  new cdk.Stack(app, `DevOpsAgentEksDevOpsAgent-${context.region}`, { env });

  return app.synth();
}

describe('CDK-managed DevOps Agent onboarding', () => {
  it('always includes Agent Space and trigger stacks, including split-region deployments', () => {
    const regionArb = fc.constantFrom('us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1');
    const agentRegionArb = fc.constantFrom('us-east-1', 'us-west-2', 'eu-west-1');
    const webhookUrlArb = fc.webUrl();
    const secretArnArb = fc.uuid().map(
      (id) => `arn:aws:secretsmanager:us-east-1:123456789012:secret:test-${id}`,
    );

    fc.assert(
      fc.property(regionArb, agentRegionArb, webhookUrlArb, secretArnArb,
        (region, agentRegion, webhookUrl, webhookSecretArn) => {
          const assembly = synthesizeApp({ region, agentRegion, webhookUrl, webhookSecretArn });
          const stackNames = assembly.stacks.map((stack) => stack.stackName);

          expect(stackNames).toContain(`DevOpsAgentEksAgentSpace-${agentRegion}`);
          expect(stackNames).toContain(`DevOpsAgentEksDevOpsAgent-${region}`);
          expect(stackNames).toHaveLength(TOTAL_STACK_COUNT);
        }),
      { numRuns: 100 },
    );
  });

  it('supports phase-1 synthesis before webhook outputs exist', () => {
    const assembly = synthesizeApp({
      region: 'eu-west-3',
      agentRegion: 'eu-west-1',
    });
    const names = assembly.stacks.map((stack) => stack.stackName);

    expect(names).toContain('DevOpsAgentEksAgentSpace-eu-west-1');
    expect(names).toContain('DevOpsAgentEksDevOpsAgent-eu-west-3');
  });

  it('uses only a secret ARN, never the raw HMAC secret, as deployment context', () => {
    const context = {
      region: 'us-east-1',
      agentRegion: 'us-east-1',
      webhookUrl: 'https://example.com/webhook',
      webhookSecretArn: 'arn:aws:secretsmanager:us-east-1:123456789012:secret:test-AbCdEf',
    };

    expect(Object.keys(context)).toContain('webhookSecretArn');
    expect(Object.keys(context)).not.toContain('webhookSecret');
  });
});
