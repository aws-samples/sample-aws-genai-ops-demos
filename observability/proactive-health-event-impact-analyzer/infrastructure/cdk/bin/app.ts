#!/usr/bin/env node
import 'source-map-support/register';
import { execSync } from 'child_process';
import * as cdk from 'aws-cdk-lib';
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';
import { HealthEventAnalyzerStack } from '../lib/health-event-analyzer-stack';
import { DevOpsAgentSpaceStack } from '../lib/devops-agent-space-stack';
import { ProductionValidationAspect } from '../lib/aspects/production-validation';

const app = new cdk.App();

// Deployment environment — read here (not only inside HealthEventAnalyzerStack)
// so DevOpsAgentSpaceStack, instantiated first, can size its log retention to
// match the same 90d/14d rule the repo-wide ProductionValidationAspect enforces
// below. HealthEventAnalyzerStack still validates this against VALID_ENVIRONMENTS.
const environment = app.node.tryGetContext('environment') ?? 'production';

// Region detection — priority order (matches shared/utils/aws-utils pattern):
// 1. Environment variable (temporary override)
// 2. AWS CLI config (persistent setting via `aws configure`)
// 3. Fallback to us-east-1 only if nothing configured
// NOTE: When monorepo shared/utils/aws-utils.ts is available, replace with:
//   import { getRegion } from '../../../../../shared/utils/aws-utils';
function getRegion(): string {
  const envRegion = process.env.CDK_DEFAULT_REGION || process.env.AWS_DEFAULT_REGION || process.env.AWS_REGION;
  if (envRegion?.trim()) return envRegion.trim();

  try {
    const cliRegion = execSync('aws configure get region', { encoding: 'utf-8' }).trim();
    if (cliRegion) return cliRegion;
  } catch {
    // AWS CLI not configured or not available — fall through
  }

  return 'us-east-1';
}

const region = getRegion();

// ─── DevOps Agent Space region ────────────────────────────────────────────────
// May differ from the infra region — an Agent Space monitors resources across
// ALL regions of the associated account, so it does not need to live with the
// rest of the stack. Resolved from CDK context (`devOpsAgentRegion`), which
// scripts/setup-wizard.ts sets from DEVOPS_AGENT_REGION before synthesizing.
// See shared/README.md ("AWS DevOps Agent Region").
const devOpsAgentRegion = app.node.tryGetContext('devOpsAgentRegion') || region;

// DevOpsAgentSpaceStack — the Agent Space itself (roles, operator app, AWS
// monitor association, eventChannel webhook). Deployed FIRST by the setup
// wizard: its outputs (webhook URL + Secrets Manager ARN) are read back via
// `aws cloudformation describe-stacks` and fed into the main stack below as
// context. This replaces the imperative `aws devops-agent` CLI flow the
// wizard used to drive by hand.
const agentSpaceStack = new DevOpsAgentSpaceStack(app, `HealthEventAnalyzerAgentSpace-${devOpsAgentRegion}`, {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: devOpsAgentRegion,
  },
  projectName: 'health-event-analyzer',
  deployEnvironment: environment,
  description: 'Proactive Health Event Impact Analyzer - DevOps Agent Space stack (Agent Space, IAM roles, operator app, webhook)',
});

const stack = new HealthEventAnalyzerStack(app, `HealthEventAnalyzerStack-${region}`, {
  description: 'Proactive Health Event Impact Analyzer - GenAI-powered AWS Health event correlation and team notification (uksb-do9bhieqqh)(tag:health-event-analyzer,observability)',
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region,
  },
  devOpsAgentWebhookUrl: app.node.tryGetContext('devOpsAgentWebhookUrl') ?? '',
  devOpsAgentWebhookSecretArn: app.node.tryGetContext('devOpsAgentWebhookSecretArn') ?? '',
  devOpsAgentRegion,
});

// ─── CDK Nag: AWS Solutions rule pack (Requirement 15.2) ──────────────────────
// Synthesis fails with non-zero exit code on Error-level findings.
cdk.Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

// ─── Custom Production Validation Aspect (Requirement 15.4) ───────────────────
// Validates: all Lambdas have DLQ, log retention matches environment
cdk.Aspects.of(app).add(new ProductionValidationAspect(stack.deployEnvironment));

// ─── CDK Nag Suppressions — documented justifications ─────────────────────────
NagSuppressions.addStackSuppressions(stack, [
  {
    id: 'AwsSolutions-IAM5',
    reason: 'Wildcard in resource ARN patterns (e.g., opsitem/*, agentspace/*, jira/*) is acceptable because the resource ID is not known at deploy time. The ARN is still scoped to account/region.',
  },
  {
    id: 'AwsSolutions-SQS3',
    reason: 'Dead letter queues do not need their own dead letter queues — they are the terminal destination for failed messages.',
  },
  {
    id: 'AwsSolutions-L1',
    reason: 'Lambda runtime NODEJS_24_X is the latest LTS runtime. CDK Nag may not yet recognize it as the latest if its rule set lags behind AWS runtime releases.',
  },
  {
    id: 'AwsSolutions-IAM4',
    reason: 'AWS managed policies (AWSLambdaBasicExecutionRole) are used by CDK-generated Lambda service roles for CloudWatch Logs access. This is the standard CDK pattern and provides minimal required permissions.',
  },
  {
    id: 'AwsSolutions-SNS2',
    reason: 'SNS topic already has KMS encryption enabled using the AWS managed key alias/aws/sns (configured in notification.ts).',
    appliesTo: ['Resource::*'],
  },
  {
    id: 'AwsSolutions-SNS3',
    reason: 'SNS topic enforces SSL via an explicit Deny statement when aws:SecureTransport is false in the topic resource policy.',
  },
  {
    id: 'AwsSolutions-SQS4',
    reason: 'SQS dead letter queues receive messages from Lambda async invocation failures via internal AWS service integration. SSL enforcement on these DLQs is not applicable as messages are published by the Lambda service, not user-initiated API calls.',
  },
], true);

// ─── CDK Nag Suppressions — DevOps Agent Space stack ───────────────────────────
NagSuppressions.addStackSuppressions(agentSpaceStack, [
  {
    id: 'AwsSolutions-IAM4',
    reason: 'AIDevOpsAgentAccessPolicy and AIDevOpsOperatorAppAccessPolicy are AWS managed policies published specifically for AWS DevOps Agent — there is no customer-managed equivalent, and AWS documents these as the required grant for the service to assume a monitoring/operator role. AWSLambdaBasicExecutionRole on the webhook provisioner Lambda is the standard CDK-generated execution role for CloudWatch Logs access.',
  },
  {
    id: 'AwsSolutions-IAM5',
    reason: 'Wildcards are required: the aidevops:RegisterService/ListServices/AssociateService/DisassociateService/ListAssociations calls the webhook provisioner Lambda makes are account-level APIs with no resource-level ARN in their request (RegisterService especially has no space/service id yet to scope to); iam:CreateServiceLinkedRole is scoped to the aws-service-role/* path, which is the finest grain IAM supports for SLR creation; and the Secrets Manager grantWrite() on the webhook secret appends AWS\'s own wildcard version suffix to an otherwise fully-scoped secret ARN.',
  },
  {
    id: 'AwsSolutions-L1',
    reason: 'Lambda runtime NODEJS_24_X is the latest LTS runtime. CDK Nag may not yet recognize it as the latest if its rule set lags behind AWS runtime releases.',
  },
  {
    id: 'AwsSolutions-SMG4',
    reason: 'This secret holds the DevOps Agent webhook HMAC key, written exactly once by the provisioning custom resource from AssociateService\'s one-time response and read only via GetSecretValue. There is no DevOps Agent rotation API for this value — the only way to rotate it is to replace the association (delete + recreate the webhook), which the custom resource already does on any property change. Automatic Secrets Manager rotation would silently desynchronize the stored value from the live webhook.',
  },
], true);
