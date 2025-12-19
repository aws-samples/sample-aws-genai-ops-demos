#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { PasswordResetInfraStack } from '../lib/infra-stack';
import { PasswordResetRuntimeStack } from '../lib/runtime-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { AuthStack } from '../lib/auth-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
};

// Infrastructure stack (ECR, IAM, CodeBuild, S3)
const infraStack = new PasswordResetInfraStack(app, 'PasswordResetInfra', {
  env,
  description: 'Password Reset Chatbot: Container registry, build pipeline, and IAM roles (uksb-do9bhieqqh)(tag:password-reset,operations-automation)',
});

// Auth stack (Cognito User Pool) - users reset passwords for accounts in this pool
const authStack = new AuthStack(app, 'PasswordResetAuth', {
  env,
  description: 'Password Reset Chatbot: Cognito User Pool (identity provider)',
});

// Runtime stack - NO JWT auth (anonymous access)
const runtimeStack = new PasswordResetRuntimeStack(app, 'PasswordResetRuntime', {
  env,
  userPoolId: authStack.userPool.userPoolId,
  userPoolClientId: authStack.userPoolClient.userPoolClientId,
  description: 'Password Reset Chatbot: AgentCore Runtime with anonymous access',
});

// Frontend stack
new FrontendStack(app, 'PasswordResetFrontend', {
  env,
  agentRuntimeArn: runtimeStack.agentRuntimeArn,
  region: env.region || 'us-east-1',
  description: 'Password Reset Chatbot: CloudFront-hosted React interface',
});

app.synth();
