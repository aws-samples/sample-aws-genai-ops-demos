#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DataStack } from '../lib/data-stack';
import { AuthStack } from '../lib/auth-stack';
import { PipelineStack } from '../lib/pipeline-stack';
import { ApiStack } from '../lib/api-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { SpokeStack } from '../lib/spoke-stack';
import { OrgStack } from '../lib/org-stack';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();

// Get region using shared utility
const region = getRegion();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: region,
};

// Optional confused-deputy guard for the multi-account scan (#144): the same
// value must be given to the hub (Pipeline stack) and to every spoke.
//   --context spokeExternalId=<opaque string>
const spokeExternalId: string | undefined = app.node.tryGetContext('spokeExternalId') || undefined;

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
  spokeExternalId,
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

// Spoke stack (multi-account scan, #144): ONE read-only role, deployed in a
// MEMBER account with that account's credentials, pointing at the hub:
//   npx cdk deploy AWSServicesLifecycleTrackerSpoke-<region> --context hubAccountId=<hub account id>
// Independent of the stacks above (nothing else of the tracker exists in a
// spoke). Without the context value it defaults to the current account so a
// synth of the whole app still works. No tracking tag: the Pipeline stack is
// the demo's single tracked stack.
new SpokeStack(app, `AWSServicesLifecycleTrackerSpoke-${region}`, {
  env,
  hubAccountId: app.node.tryGetContext('hubAccountId') || process.env.CDK_DEFAULT_ACCOUNT || '000000000000',
  externalId: spokeExternalId,
  synthesizer: new cdk.BootstraplessSynthesizer(), // one IAM role, no assets: no `cdk bootstrap` in the spoke
  description: 'AWS Services Lifecycle Tracker Spoke: read-only scan role assumed by the hub account',
});

// Org stack (multi-account scan, #144): StackSet rolling the spoke role out to
// every account of the organization root / OUs, deployed FROM the hub. Only
// instantiated when targets are given, so single-account users never see it:
//   npx cdk deploy AWSServicesLifecycleTrackerOrg-<region> --context orgTargets=r-xxxx[,ou-xxxx-yyyyyyyy]
// Requires StackSets trusted access and the hub to be the management account
// or a StackSets delegated administrator (shared/scripts/check-org-access).
const orgTargets: string = app.node.tryGetContext('orgTargets') || '';
if (orgTargets) {
  new OrgStack(app, `AWSServicesLifecycleTrackerOrg-${region}`, {
    env,
    hubAccountId: process.env.CDK_DEFAULT_ACCOUNT || '',
    targetOuIds: orgTargets.split(',').map((s: string) => s.trim()).filter(Boolean),
    externalId: spokeExternalId,
    description: 'AWS Services Lifecycle Tracker Org: StackSet placing the read-only spoke role in every member account',
  });
}

app.synth();
