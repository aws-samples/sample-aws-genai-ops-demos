#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AWSServicesLifecycleTrackerInfraStack } from '../lib/infra-stack';
import { DataStack } from '../lib/data-stack';
import { AgentCoreStack } from '../lib/runtime-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { AuthStack } from '../lib/auth-stack';
import { AWSServicesLifecycleTrackerScheduler } from '../lib/scheduler-stack';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();

// Get region using shared utility
const region = getRegion();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: region,
};

// Infrastructure stack (ECR, IAM, CodeBuild, S3)
new AWSServicesLifecycleTrackerInfraStack(app, `AWSServicesLifecycleTrackerInfra-${region}`, {
  env,
  description: 'AWS Services Lifecycle Tracker Infrastructure: Container registry, build pipeline, and IAM roles',
});

// Data stack (DynamoDB tables)
const dataStack = new DataStack(app, `AWSServicesLifecycleTrackerData-${region}`, {
  env,
  description: 'AWS Services Lifecycle Tracker Data: DynamoDB tables for deprecation data and configuration',
});

// Auth stack (Cognito User Pool)
const authStack = new AuthStack(app, `AWSServicesLifecycleTrackerAuth-${region}`, {
  env,
  description: 'AWS Services Lifecycle Tracker Authentication: Cognito User Pool for admin access',
});

// Runtime stack (depends on infra, auth, and data stacks)
const agentStack = new AgentCoreStack(app, `AWSServicesLifecycleTrackerRuntime-${region}`, {
  env,
  userPool: authStack.userPool,
  userPoolClient: authStack.userPoolClient,
  lifecycleTableName: dataStack.lifecycleTable.tableName,
  configTableName: dataStack.configTable.tableName,
  description: 'AWS Services Lifecycle Tracker Runtime: AI-powered extraction agent with built-in authentication (uksb-do9bhieqqh)(tag:lifecycle-tracker,operations-automation)',
});

// Scheduler stack (depends on runtime stack)
const schedulerStack = new AWSServicesLifecycleTrackerScheduler(app, `AWSServicesLifecycleTrackerScheduler-${region}`, {
  env,
  agentRuntimeArn: agentStack.agentRuntimeArn,
  description: 'AWS Services Lifecycle Tracker Scheduler: EventBridge rules for automated extraction scheduling',
});

// Ensure scheduler stack depends on runtime stack
schedulerStack.addDependency(agentStack);

// Frontend stack (depends on runtime, auth, and data stacks)
new FrontendStack(app, `AWSServicesLifecycleTrackerFrontend-${region}`, {
  env,
  userPoolId: authStack.userPool.userPoolId,
  userPoolClientId: authStack.userPoolClient.userPoolClientId,
  agentRuntimeArn: agentStack.agentRuntimeArn,
  region: region,
  lifecycleTableName: dataStack.lifecycleTable.tableName,
  configTableName: dataStack.configTable.tableName,
  userPool: authStack.userPool,
  description: 'AWS Services Lifecycle Tracker Frontend: Admin interface with direct AgentCore integration',
});

app.synth();
