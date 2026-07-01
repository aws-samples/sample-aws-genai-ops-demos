#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DataStack } from '../lib/data-stack';
import { AuthStack } from '../lib/auth-stack';
import { ScannerStack } from '../lib/scanner-stack';
import { IngestStack } from '../lib/ingest-stack';
import { DevOpsAgentStack } from '../lib/devops-agent-stack';
import { ApiStack } from '../lib/api-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();

const region = getRegion();
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region,
};

// Context values — passed from deploy-all.sh. Empty strings are placeholders for
// first-deploy (before the DevOps Agent webhook has been generated).
const webhookUrl = app.node.tryGetContext('devOpsAgentWebhookUrl') || '';
const webhookSecret = app.node.tryGetContext('devOpsAgentWebhookSecret') || '';
const devOpsAgentRegion = app.node.tryGetContext('devOpsAgentRegion') || 'us-east-1';
const devOpsAgentSpaceId = app.node.tryGetContext('devOpsAgentSpaceId') || '';
// Default to the Nova Lite 2 global inference profile. Direct on-demand
// model IDs fail with "isn't supported" in most regions; the `global.*`
// profile routes to the closest supported region automatically, so the demo
// works out of the box regardless of where it is deployed. Override with
// -c bedrockModelId=... for non-standard models or custom profiles.
const bedrockModelId = app.node.tryGetContext('bedrockModelId') || 'global.amazon.nova-2-lite-v1:0';
const scanSchedule = app.node.tryGetContext('scanSchedule') || 'cron(0 6 * * ? *)';
// autoInvestigate=true → ingest Lambda auto-publishes every CRITICAL/HIGH to the
// DevOps Agent webhook. Default false: TAM-driven, click a finding to investigate.
const autoInvestigate = (app.node.tryGetContext('autoInvestigate') || 'false') === 'true';

// 1. Data — DynamoDB findings table + S3 raw-reports and remediations buckets
const dataStack = new DataStack(app, `ProwlerSecurityData-${region}`, {
  env,
  description: 'Prowler Security Findings: DynamoDB findings table + S3 buckets for raw reports and Bedrock remediations',
});

// 2. Auth — Cognito User Pool + Identity Pool (dashboard login)
const authStack = new AuthStack(app, `ProwlerSecurityAuth-${region}`, {
  env,
  description: 'Prowler Security Findings: Cognito User Pool + Identity Pool for dashboard authentication',
});

// 3. DevOps Agent — SNS topic + HMAC Lambda + Secrets Manager webhook secret
const devOpsAgentStack = new DevOpsAgentStack(app, `ProwlerSecurityDevOpsAgent-${region}`, {
  env,
  webhookUrl,
  webhookSecret,
  devOpsAgentRegion,
  devOpsAgentSpaceId,
  remediationsBucketName: dataStack.remediationsBucket.bucketName,
  costEventsTableName: dataStack.costEventsTable.tableName,
  description: 'Prowler Security Findings: SNS + Lambda bridge to AWS DevOps Agent webhook',
});
devOpsAgentStack.addDependency(dataStack);

// 4. Scanner — Prowler ECR + ECS Fargate + EventBridge schedule + CodeBuild image build
// Main stack → carries the Solution Adoption Tracking ID for the whole demo.
const scannerStack = new ScannerStack(app, `ProwlerSecurityScanner-${region}`, {
  env,
  rawReportsBucketName: dataStack.rawReportsBucket.bucketName,
  scanSchedule,
  description: 'Prowler Security Findings + DevOps Agent + Bedrock Nova Lite remediation (uksb-do9bhieqqh)(tag:prowler-security-findings-agent,security)',
});
scannerStack.addDependency(dataStack);

// 5. Ingest — S3 event → ingest-findings Lambda → DynamoDB + remediation-context Lambda (Bedrock Nova Lite) + SNS fan-out
const ingestStack = new IngestStack(app, `ProwlerSecurityIngest-${region}`, {
  env,
  findingsTableName: dataStack.findingsTable.tableName,
  rawReportsBucketName: dataStack.rawReportsBucket.bucketName,
  remediationsBucketName: dataStack.remediationsBucket.bucketName,
  costEventsTableName: dataStack.costEventsTable.tableName,
  devOpsAgentTriggerTopicArn: devOpsAgentStack.triggerTopicArn,
  bedrockModelId,
  autoInvestigate,
  description: 'Prowler Security Findings: ingest Lambda + Bedrock Nova Lite remediation context + SNS fan-out',
});
ingestStack.addDependency(dataStack);
ingestStack.addDependency(devOpsAgentStack);

// 6. API — dashboard-api Lambda with Function URL (IAM auth, SigV4 from browser)
const apiStack = new ApiStack(app, `ProwlerSecurityApi-${region}`, {
  env,
  findingsTableName: dataStack.findingsTable.tableName,
  costEventsTableName: dataStack.costEventsTable.tableName,
  remediationsBucketName: dataStack.remediationsBucket.bucketName,
  rawReportsBucketName: dataStack.rawReportsBucket.bucketName,
  scannerClusterArn: scannerStack.clusterArn,
  scannerTaskDefinitionArn: scannerStack.taskDefinitionArn,
  scannerSubnetIds: scannerStack.subnetIds,
  scannerSecurityGroupId: scannerStack.securityGroupId,
  scannerLogGroupName: scannerStack.logGroupName,
  authenticatedRoleArn: authStack.authenticatedRoleArn,
  devOpsAgentTopicArn: devOpsAgentStack.triggerTopicArn,
  devOpsAgentRegion,
  devOpsAgentSpaceId,
  devOpsAgentSecretArn: devOpsAgentStack.webhookSecretArn,
  remediationLambdaArn: ingestStack.remediationLambdaArn,
  remediationLambdaName: ingestStack.remediationLambdaName,
  description: 'Prowler Security Findings: dashboard-api Lambda URL (SigV4) for the React dashboard',
});
apiStack.addDependency(dataStack);
apiStack.addDependency(authStack);
apiStack.addDependency(scannerStack);
apiStack.addDependency(devOpsAgentStack);
apiStack.addDependency(ingestStack);

// 7. Observability — CloudWatch Dashboard that stitches together the demo's
//    Lambda/Bedrock/DynamoDB/Fargate metrics in one pane. Deployed last among
//    the non-Frontend stacks so all function/table/cluster names exist.
const observabilityStack = new ObservabilityStack(app, `ProwlerSecurityObservability-${region}`, {
  env,
  lambdaNames: {
    ingest: 'prowler-security-ingest',
    remediationContext: 'prowler-security-remediation-context',
    devOpsTrigger: 'prowler-security-devops-trigger',
    dashboardApi: 'prowler-security-dashboard-api',
  },
  tableNames: {
    findings: dataStack.findingsTable.tableName,
    costEvents: dataStack.costEventsTable.tableName,
  },
  scannerClusterName: 'prowler-security-scanner',
  bedrockModelId,
  description: 'Prowler Security Findings: CloudWatch dashboard for Lambda / Bedrock / DynamoDB / Fargate health',
});
observabilityStack.addDependency(ingestStack);
observabilityStack.addDependency(devOpsAgentStack);
observabilityStack.addDependency(apiStack);
observabilityStack.addDependency(scannerStack);

// 8. Frontend — CloudFront + S3 + OAC hosting the React/Cloudscape dashboard
new FrontendStack(app, `ProwlerSecurityFrontend-${region}`, {
  env,
  userPoolId: authStack.userPool.userPoolId,
  userPoolClientId: authStack.userPoolClient.userPoolClientId,
  identityPoolId: authStack.identityPool.ref,
  apiFunctionUrl: apiStack.functionUrl,
  region,
  description: 'Prowler Security Findings: CloudFront + S3 dashboard with SigV4 to dashboard-api',
});

app.synth();
