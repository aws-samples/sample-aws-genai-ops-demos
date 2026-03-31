/**
 * Feature: cfn-to-cdk-migration, Property 4: Resource type parity
 *
 * For each stack pair (CDK stack vs original YAML), verify CDK-synthesized
 * resource types are a superset of original YAML resource types.
 *
 * Validates: Requirements 12.1
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
 * Reference resource types extracted from the original CloudFormation YAML
 * templates. Each entry maps a CDK stack to the set of unique AWS resource
 * types that appeared in the corresponding YAML file(s).
 */
const YAML_RESOURCE_TYPES: Record<string, string[]> = {
  network: [
    'AWS::EC2::VPC',
    'AWS::EC2::InternetGateway',
    'AWS::EC2::VPCGatewayAttachment',
    'AWS::EC2::Subnet',
    'AWS::EC2::EIP',
    'AWS::EC2::NatGateway',
    'AWS::EC2::RouteTable',
    'AWS::EC2::Route',
    'AWS::EC2::SubnetRouteTableAssociation',
    'AWS::EC2::SecurityGroup',
    'AWS::EC2::SecurityGroupIngress',
    'AWS::EC2::SecurityGroupEgress',
  ],
  database: [
    'AWS::RDS::DBSubnetGroup',
    'AWS::RDS::DBParameterGroup',
    'AWS::RDS::DBInstance',
  ],
  compute: [
    'AWS::IAM::Role',
    'AWS::EKS::Cluster',
    'AWS::EKS::Nodegroup',
    'AWS::IAM::OIDCProvider',
  ],
  pipeline: [
    'AWS::ECR::Repository',
    'AWS::IAM::Role',
    'AWS::CodeBuild::Project',
  ],
  auth: [
    'AWS::Cognito::UserPool',
    'AWS::Cognito::UserPoolDomain',
    'AWS::Cognito::UserPoolClient',
    'AWS::Cognito::UserPoolResourceServer',
    'AWS::Cognito::UserPoolGroup',
    'AWS::Logs::LogGroup',
  ],
  frontend: [
    'AWS::S3::Bucket',
    'AWS::S3::BucketPolicy',
    'AWS::CloudFront::OriginAccessControl',
    'AWS::CloudFront::CachePolicy',
    'AWS::CloudFront::ResponseHeadersPolicy',
    'AWS::CloudFront::Distribution',
  ],
  monitoring: [
    'AWS::SNS::Topic',
    'AWS::SNS::TopicPolicy',
    'AWS::Logs::LogGroup',
    'AWS::Logs::MetricFilter',
    'AWS::CloudWatch::Alarm',
  ],
  devopsAgent: [
    'AWS::SecretsManager::Secret',
    'AWS::SNS::Topic',
    'AWS::IAM::Role',
    'AWS::Lambda::Function',
    'AWS::SNS::Subscription',
    'AWS::Lambda::Permission',
  ],
};

/** Extract unique resource types from a synthesized CDK template. */
function getResourceTypes(template: Template): Set<string> {
  const json = template.toJSON();
  const resources = json.Resources ?? {};
  return new Set(Object.values(resources).map((r: any) => r.Type as string));
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
    network: Template.fromStack(net),
    database: Template.fromStack(db),
    compute: Template.fromStack(compute),
    pipeline: Template.fromStack(pipeline),
    auth: Template.fromStack(auth),
    frontend: Template.fromStack(frontend),
    monitoring: Template.fromStack(monitoring),
    devopsAgent: Template.fromStack(devops),
  };
}

describe('Property 4: Resource type parity', () => {
  it('CDK-synthesized resource types are a superset of original YAML resource types', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');
    const archArb = fc.constantFrom('arm64', 'amd64');

    fc.assert(
      fc.property(envArb, archArb, (env, arch) => {
        const templates = synthesizeAll(env, arch);

        for (const [stackKey, expectedTypes] of Object.entries(YAML_RESOURCE_TYPES)) {
          const template = templates[stackKey as keyof typeof templates];
          const actualTypes = getResourceTypes(template);

          for (const expectedType of expectedTypes) {
            expect(actualTypes).toContain(expectedType);
          }
        }
      }),
      { numRuns: 100 },
    );
  });
});
