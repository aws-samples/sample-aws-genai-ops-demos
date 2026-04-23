#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { execSync } from 'child_process';
import { NetworkStack } from '../lib/network-stack';
import { AuthStack } from '../lib/auth-stack';
import { DatabaseStack } from '../lib/database-stack';
import { ComputeStack } from '../lib/compute-stack';
import { PipelineStack } from '../lib/pipeline-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { MonitoringStack } from '../lib/monitoring-stack';
import { DevOpsAgentStack } from '../lib/devops-agent-stack';
import { FailureSimulatorApiStack } from '../lib/failure-simulator-api-stack';
import { McpServerStack } from '../lib/mcp-server-stack';

// ---------------------------------------------------------------------------
// Region detection — matches shared/scripts/check-prerequisites.sh priority:
//   1. AWS_DEFAULT_REGION or AWS_REGION env var
//   2. AWS CLI config (aws configure get region)
//   3. Fallback to us-east-1
// ---------------------------------------------------------------------------
function getRegion(): string {
  const envRegion = process.env.AWS_DEFAULT_REGION || process.env.AWS_REGION;
  if (envRegion) return envRegion;

  try {
    const cliRegion = execSync('aws configure get region', { encoding: 'utf-8' }).trim();
    if (cliRegion) return cliRegion;
  } catch {
    // aws cli not configured — fall through
  }

  return 'us-east-1';
}

const region = getRegion();

const app = new cdk.App();

// ---------------------------------------------------------------------------
// CDK context parameters
// ---------------------------------------------------------------------------
const environment = app.node.tryGetContext('environment') ?? 'dev';
const projectName = app.node.tryGetContext('projectName') ?? 'devops-agent-eks';
const eksNodeArchitecture = app.node.tryGetContext('eksNodeArchitecture') ?? 'arm64';
const eksNodeInstanceType = app.node.tryGetContext('eksNodeInstanceType') ?? 't4g.medium';
const eksNodeDesiredCapacity = Number(app.node.tryGetContext('eksNodeDesiredCapacity') ?? '2');
const devOpsAgentWebhookUrl = app.node.tryGetContext('devOpsAgentWebhookUrl') ?? '';
const devOpsAgentWebhookSecret = app.node.tryGetContext('devOpsAgentWebhookSecret') ?? '';
const apiGatewayEndpoint = app.node.tryGetContext('apiGatewayEndpoint') ?? '';

const env: cdk.Environment = { region };

// ---------------------------------------------------------------------------
// Stack instantiation with cross-stack references via typed props
// ---------------------------------------------------------------------------

// NetworkStack — primary stack with solution adoption tracking
const networkStack = new NetworkStack(app, `DevOpsAgentEksNetwork-${region}`, {
  env,
  environment,
  projectName,
  description: `DevOps Agent EKS Demo Network Stack (uksb-do9bhieqqh)(tag:devops-agent-eks,observability)`,
});

const authStack = new AuthStack(app, `DevOpsAgentEksAuth-${region}`, {
  env,
  environment,
  projectName,
  description: 'DevOps Agent EKS Demo Auth Stack',
});

// DatabaseStack — depends on NetworkStack (vpc, data subnets, DB security group)
const databaseStack = new DatabaseStack(app, `DevOpsAgentEksDatabase-${region}`, {
  env,
  environment,
  projectName,
  vpc: networkStack.vpc,
  privateDataSubnets: networkStack.privateDataSubnets,
  databaseSecurityGroup: networkStack.databaseSecurityGroup,
  description: 'DevOps Agent EKS Demo Database Stack',
});

// ComputeStack — depends on NetworkStack (compute subnets, EKS security group)
const computeStack = new ComputeStack(app, `DevOpsAgentEksCompute-${region}`, {
  env,
  environment,
  projectName,
  privateComputeSubnets: networkStack.privateComputeSubnets,
  eksSecurityGroup: networkStack.eksSecurityGroup,
  nodeInstanceType: eksNodeInstanceType,
  nodeArchitecture: eksNodeArchitecture,
  nodeDesiredCapacity: eksNodeDesiredCapacity,
  description: 'DevOps Agent EKS Demo Compute Stack',
});

// PipelineStack — uses architecture context for CodeBuild compute type
const pipelineStack = new PipelineStack(app, `DevOpsAgentEksPipeline-${region}`, {
  env,
  environment,
  projectName,
  eksNodeArchitecture,
  description: 'DevOps Agent EKS Demo Pipeline Stack',
});

const monitoringStack = new MonitoringStack(app, `DevOpsAgentEksMonitoring-${region}`, {
  env,
  environment,
  projectName,
  description: 'DevOps Agent EKS Demo Monitoring Stack',
});

// DevOpsAgentStack — trigger Lambda + SNS + Secrets Manager (infra region)
// The Agent Space itself is created by scripts/setup-devops-agent.ps1 in the
// DevOps Agent region (e.g. us-east-1) via native AWS CLI — not via CDK,
// because AWS::DevOpsAgent resources are only available in supported regions.
const devOpsAgentStack = new DevOpsAgentStack(app, `DevOpsAgentEksDevOpsAgent-${region}`, {
  env,
  environment,
  projectName,
  eksClusterName: computeStack.clusterName,
  webhookUrl: devOpsAgentWebhookUrl,
  webhookSecret: devOpsAgentWebhookSecret,
  criticalAlarmsTopicArn: monitoringStack.criticalAlarmsTopicArn,
  description: 'DevOps Agent EKS Demo DevOps Agent Stack',
});

// FailureSimulatorApiStack — Lambda-based failure simulator API (outside EKS cluster)
// agentSpaceId comes from context (set by deploy script after setup-devops-agent creates it)
const failureSimulatorApiStack = new FailureSimulatorApiStack(app, `DevOpsAgentEksFailureSimulatorApi-${region}`, {
  env,
  environment,
  projectName,
  vpc: networkStack.vpc,
  privateComputeSubnets: networkStack.privateComputeSubnets,
  eksSecurityGroup: networkStack.eksSecurityGroup,
  eksClusterName: computeStack.clusterName,
  alarmName: `${projectName}-${environment}-database-connection-errors`,
  devOpsAgentRegion: app.node.tryGetContext('devOpsAgentRegion') || 'us-east-1',
  devOpsAgentSpaceId: app.node.tryGetContext('devOpsAgentSpaceId') || '',
  description: 'DevOps Agent EKS Demo Failure Simulator API Stack',
});

// McpServerStack — infrastructure for the Payment Transaction Insights MCP server
// hosted on AgentCore Runtime. Provides read-only access to the payment database
// so DevOps Agent can assess business impact during incidents.
const mcpServerStack = new McpServerStack(app, `DevOpsAgentEksMcpServer-${region}`, {
  env,
  environment,
  projectName,
  vpc: networkStack.vpc,
  privateComputeSubnets: networkStack.privateComputeSubnets,
  databaseSecurityGroup: networkStack.databaseSecurityGroup,
  rdsEndpoint: databaseStack.rdsEndpoint,
  description: 'DevOps Agent EKS Demo MCP Server Infrastructure Stack',
});

// FrontendStack depends on FailureSimulatorApi (adminApiId, adminApiStageName)
const frontendStack = new FrontendStack(app, `DevOpsAgentEksFrontend-${region}`, {
  env,
  environment,
  projectName,
  apiGatewayEndpoint: apiGatewayEndpoint || undefined,
  adminApiId: failureSimulatorApiStack.apiId,
  adminApiStageName: failureSimulatorApiStack.apiStageName,
  description: 'DevOps Agent EKS Demo Frontend Stack',
});
