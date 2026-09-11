#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DataStack } from '../lib/data-stack';
import { AuthStack } from '../lib/auth-stack';
import { PipelineStack } from '../lib/pipeline-stack';
import { ApiStack } from '../lib/api-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();

// Get region using shared utility
const region = getRegion();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: region,
};

// Data stack (DynamoDB tables)
const dataStack = new DataStack(app, `AWSServicesLifecycleTrackerData-${region}`, {
  env,
  description: 'AWS Services Lifecycle Tracker Data: DynamoDB tables for deprecation data, inventory and configuration',
});

// Auth stack (Cognito User Pool)
const authStack = new AuthStack(app, `AWSServicesLifecycleTrackerAuth-${region}`, {
  env,
  description: 'AWS Services Lifecycle Tracker Authentication: Cognito User Pool for admin access',
});

// Pipeline stack (main stack: durable refresh pipeline + API function + schedules)
const pipelineStack = new PipelineStack(app, `AWSServicesLifecycleTrackerPipeline-${region}`, {
  env,
  lifecycleTable: dataStack.lifecycleTable,
  configTable: dataStack.configTable,
  stateTable: dataStack.stateTable,
  inventoryTable: dataStack.inventoryTable,
  actionPlanTable: dataStack.actionPlanTable,
  description: 'AWS Services Lifecycle Tracker Pipeline: Lambda durable function refreshing deprecation data and account inventory (uksb-do9bhieqqh)(tag:lifecycle-tracker,operations-automation)',
});

// API stack (HTTP API + Cognito JWT authorizer in front of the API function)
const apiStack = new ApiStack(app, `AWSServicesLifecycleTrackerApi-${region}`, {
  env,
  apiFunction: pipelineStack.apiFunction,
  userPool: authStack.userPool,
  userPoolClient: authStack.userPoolClient,
  description: 'AWS Services Lifecycle Tracker API: HTTP API with Cognito JWT authorization for the admin interface',
});

// Frontend stack (static SPA; build must already contain the API URL)
new FrontendStack(app, `AWSServicesLifecycleTrackerFrontend-${region}`, {
  env,
  userPoolId: authStack.userPool.userPoolId,
  userPoolClientId: authStack.userPoolClient.userPoolClientId,
  apiUrl: apiStack.apiUrl,
  region: region,
  description: 'AWS Services Lifecycle Tracker Frontend: Admin interface (S3 + CloudFront)',
});

app.synth();
