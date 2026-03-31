/**
 * Feature: cfn-to-cdk-migration, Property 5: Output key parity
 *
 * For each output key queried by deployment scripts, verify it exists in the
 * corresponding CDK stack's synthesized template.
 *
 * Validates: Requirements 12.3
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { NetworkStack } from '../lib/network-stack';
import { DatabaseStack } from '../lib/database-stack';
import { ComputeStack } from '../lib/compute-stack';
import { PipelineStack } from '../lib/pipeline-stack';
import { AuthStack } from '../lib/auth-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { MonitoringStack } from '../lib/monitoring-stack';
import { DevOpsAgentStack } from '../lib/devops-agent-stack';

/**
 * Required output keys per CDK stack, derived from the design document's
 * CloudFormation Output Mapping table. These are the keys that deployment
 * scripts query via `aws cloudformation describe-stacks`.
 */
const REQUIRED_OUTPUTS: Record<string, string[]> = {
  compute: ['EksClusterName', 'EksClusterEndpoint', 'OIDCProviderArn'],
  database: ['RdsEndpoint'],
  auth: ['UserPoolId', 'UserPoolClientId'],
  frontend: ['CloudFrontDistributionId', 'CloudFrontDomainName', 'WebsiteBucketName'],
  pipeline: [
    'MerchantGatewayBuildProject',
    'PaymentProcessorBuildProject',
    'WebhookServiceBuildProject',
  ],
  monitoring: ['CriticalAlarmsTopicArn'],
  devopsAgent: ['LambdaFunctionArn'],
};

/** Extract output logical IDs from a synthesized CDK template. */
function getOutputKeys(template: Template): Set<string> {
  const json = template.toJSON();
  return new Set(Object.keys(json.Outputs ?? {}));
}

/** Synthesize all stacks and return templates keyed by stack name. */
function synthesizeAll(env: string, arch: string) {
  const app = new cdk.App();
  const region = 'us-east-1';
  const cdkEnv = { region };
  const projectName = 'devops-agent-eks';

  const net = new NetworkStack(app, `DevOpsAgentEksNetwork-${region}`, {
    env: cdkEnv, environment: env, projectName,
  });
  const db = new DatabaseStack(app, `DevOpsAgentEksDatabase-${region}`, {
    env: cdkEnv, environment: env, projectName,
    vpc: net.vpc, privateDataSubnets: net.privateDataSubnets,
    databaseSecurityGroup: net.databaseSecurityGroup,
  });
  const compute = new ComputeStack(app, `DevOpsAgentEksCompute-${region}`, {
    env: cdkEnv, environment: env, projectName,
    privateComputeSubnets: net.privateComputeSubnets,
    eksSecurityGroup: net.eksSecurityGroup,
    nodeInstanceType: arch === 'arm64' ? 't4g.medium' : 't3.medium',
    nodeArchitecture: arch, nodeDesiredCapacity: 2,
  });
  const pipeline = new PipelineStack(app, `DevOpsAgentEksPipeline-${region}`, {
    env: cdkEnv, environment: env, projectName, eksNodeArchitecture: arch,
  });
  const auth = new AuthStack(app, `DevOpsAgentEksAuth-${region}`, {
    env: cdkEnv, environment: env, projectName,
  });
  const frontend = new FrontendStack(app, `DevOpsAgentEksFrontend-${region}`, {
    env: cdkEnv, environment: env, projectName,
  });
  const monitoring = new MonitoringStack(app, `DevOpsAgentEksMonitoring-${region}`, {
    env: cdkEnv, environment: env, projectName,
  });
  const devops = new DevOpsAgentStack(app, `DevOpsAgentEksDevOpsAgent-${region}`, {
    env: cdkEnv, environment: env, projectName,
    eksClusterName: compute.clusterName,
    webhookUrl: 'https://example.com/webhook',
    webhookSecret: 'test-secret',
    criticalAlarmsTopicArn: monitoring.criticalAlarmsTopicArn,
  });

  return {
    compute: Template.fromStack(compute),
    database: Template.fromStack(db),
    auth: Template.fromStack(auth),
    frontend: Template.fromStack(frontend),
    pipeline: Template.fromStack(pipeline),
    monitoring: Template.fromStack(monitoring),
    devopsAgent: Template.fromStack(devops),
  };
}

describe('Property 5: Output key parity', () => {
  it('each CDK stack contains the output keys queried by deployment scripts', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');
    const archArb = fc.constantFrom('arm64', 'amd64');

    fc.assert(
      fc.property(envArb, archArb, (env, arch) => {
        const templates = synthesizeAll(env, arch);

        for (const [stackKey, requiredKeys] of Object.entries(REQUIRED_OUTPUTS)) {
          const template = templates[stackKey as keyof typeof templates];
          const actualKeys = getOutputKeys(template);

          for (const key of requiredKeys) {
            expect(actualKeys).toContain(key);
          }
        }
      }),
      { numRuns: 100 },
    );
  });
});
