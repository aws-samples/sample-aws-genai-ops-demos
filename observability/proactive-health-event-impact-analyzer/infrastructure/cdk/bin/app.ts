#!/usr/bin/env node
import 'source-map-support/register';
import { execSync } from 'child_process';
import * as cdk from 'aws-cdk-lib';
import { AwsSolutionsChecks, NagSuppressions } from 'cdk-nag';
import { HealthEventAnalyzerStack } from '../lib/health-event-analyzer-stack';
import { ProductionValidationAspect } from '../lib/aspects/production-validation';

const app = new cdk.App();

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

const stack = new HealthEventAnalyzerStack(app, `HealthEventAnalyzerStack-${region}`, {
  description: 'Proactive Health Event Impact Analyzer - GenAI-powered AWS Health event correlation and team notification (uksb-do9bhieqqh)(tag:health-event-analyzer,resilience)',
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region,
  },
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
