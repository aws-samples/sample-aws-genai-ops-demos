#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { PasswordResetInfraStack } from '../lib/infra-stack';
import { PasswordResetRuntimeStack } from '../lib/runtime-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { AuthStack } from '../lib/auth-stack';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();

// Get region using shared utility
const region = getRegion();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: region,
};

// Infrastructure stack (ECR, IAM, CodeBuild, S3)
const infraStack = new PasswordResetInfraStack(app, `PasswordResetInfra-${region}`, {
  env,
  description: 'Password Reset Chatbot: Container registry, build pipeline, and IAM roles (uksb-do9bhieqqh)(tag:password-reset,operations-automation)',
});

// Auth stack (Cognito User Pool) - users reset passwords for accounts in this pool
const authStack = new AuthStack(app, `PasswordResetAuth-${region}`, {
  env,
  description: 'Password Reset Chatbot: Cognito User Pool (identity provider)',
});

// Runtime stack - NO JWT auth (anonymous access)
const runtimeStack = new PasswordResetRuntimeStack(app, `PasswordResetRuntime-${region}`, {
  env,
  userPoolId: authStack.userPool.userPoolId,
  userPoolClientId: authStack.userPoolClient.userPoolClientId,
  description: 'Password Reset Chatbot: AgentCore Runtime with anonymous access',
});

// Frontend stack
new FrontendStack(app, `PasswordResetFrontend-${region}`, {
  env,
  agentRuntimeArn: runtimeStack.agentRuntimeArn,
  region: region,
  description: 'Password Reset Chatbot: CloudFront-hosted React interface',
});

app.synth();
