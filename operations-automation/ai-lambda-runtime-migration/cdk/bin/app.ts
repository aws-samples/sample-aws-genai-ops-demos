#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DataStack } from '../lib/data-stack';
import { AuthStack } from '../lib/auth-stack';
import { RuntimeStack } from '../lib/runtime-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();

// Get region using shared utility
const region = getRegion();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: region,
};

// Data stack (DynamoDB + S3)
const dataStack = new DataStack(app, `LambdaRuntimeMigrationData-${region}`, {
  env,
  description: 'Lambda Runtime Migration: DynamoDB inventory table and S3 bucket for migration artifacts',
});

// Auth stack (Cognito User Pool, Client, Identity Pool)
const authStack = new AuthStack(app, `LambdaRuntimeMigrationAuth-${region}`, {
  env,
  description: 'Lambda Runtime Migration: Cognito User Pool and Identity Pool for dashboard authentication',
});

// Runtime stack (3 dedicated AgentCore Runtimes) — solution adoption tracking here only
const runtimeStack = new RuntimeStack(app, `LambdaRuntimeMigrationRuntime-${region}`, {
  env,
  tableName: dataStack.table.tableName,
  bucketName: dataStack.bucket.bucketName,
  description: 'AI Lambda Runtime Migration Assistant (uksb-do9bhieqqh)(tag:lambda-runtime-migration,operations-automation)',
});

// Frontend stack (S3 + CloudFront — no API Gateway, no Lambda)
new FrontendStack(app, `LambdaRuntimeMigrationFrontend-${region}`, {
  env,
  userPoolId: authStack.userPool.userPoolId,
  userPoolClientId: authStack.userPoolClient.userPoolClientId,
  identityPoolId: authStack.identityPool.ref,
  discoverRuntimeArn: runtimeStack.discoverRuntimeArn,
  analyzeRuntimeArn: runtimeStack.analyzeRuntimeArn,
  transformRuntimeArn: runtimeStack.transformRuntimeArn,
  region: region,
  description: 'Lambda Runtime Migration: CloudFront dashboard with direct AgentCore SigV4 access',
});

app.synth();
