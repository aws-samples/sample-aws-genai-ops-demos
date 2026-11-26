#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DataStack } from '../lib/data-stack';
import { AuthStack } from '../lib/auth-stack';
import { PipelineStack } from '../lib/pipeline-stack';
import { ApiStack } from '../lib/api-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { novaLiteModelId } from '../lib/model-id';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();
const region = getRegion();
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };
// -c modelId=<inference profile id> overrides the Amazon Nova Lite profile (the IAM grant builds an
// inference-profile ARN from it, so a plain foundation model id does not work); -c modelId=off (or none)
// disables the AI steps.
const modelContext: string | undefined = app.node.tryGetContext('modelId') || undefined;
const modelId = modelContext && ['off', 'none'].includes(modelContext.toLowerCase())
  ? ''
  : novaLiteModelId(region, modelContext);

const data = new DataStack(app, `S3DataProtectionData-${region}`, {
  env,
  description: 'S3 Data Protection Advisor: reports bucket and runs table (uksb-do9bhieqqh)(tag:ai-s3-data-protection-advisor,resilience)',
});

const auth = new AuthStack(app, `S3DataProtectionAuth-${region}`, {
  env,
  description: 'S3 Data Protection Advisor: Cognito user pool for dashboard sign-in',
});

const pipeline = new PipelineStack(app, `S3DataProtectionPipeline-${region}`, {
  env,
  table: data.table,
  bucket: data.bucket,
  modelId,
  description: 'S3 Data Protection Advisor: durable scan pipeline and API functions',
});

const api = new ApiStack(app, `S3DataProtectionApi-${region}`, {
  env,
  apiFunction: pipeline.apiFunction,
  userPool: auth.userPool,
  userPoolClient: auth.userPoolClient,
  description: 'S3 Data Protection Advisor: HTTP API with Cognito JWT authorizer',
});

new FrontendStack(app, `S3DataProtectionFrontend-${region}`, {
  env,
  apiId: api.apiId,
  userPool: auth.userPool,
  userPoolClient: auth.userPoolClient,
  description: 'S3 Data Protection Advisor: CloudFront dashboard served from S3',
});

app.synth();
