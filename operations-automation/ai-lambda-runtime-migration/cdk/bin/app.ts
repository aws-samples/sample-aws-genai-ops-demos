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
import { MockFixtureStack } from '../lib/mock-fixture-stack';
import { TransformStack } from '../lib/transform-stack';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();

// Get region using shared utility
const region = getRegion();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: region,
};

// Optional confused-deputy guard for the multi-account scan: the
// same value must be given to the hub (Pipeline stack) and to every spoke.
//   --context spokeExternalId=<opaque string>
const spokeExternalId: string | undefined = app.node.tryGetContext('spokeExternalId') || undefined;

// Data stack (DynamoDB + S3)
const dataStack = new DataStack(app, `LambdaRuntimeMigrationData-${region}`, {
  env,
  description: 'Lambda Runtime Migration: DynamoDB inventory table and S3 bucket for migration artifacts',
});

// Auth stack (Cognito User Pool + Client; JWT authorizer, no Identity Pool)
const authStack = new AuthStack(app, `LambdaRuntimeMigrationAuth-${region}`, {
  env,
  description: 'Lambda Runtime Migration: Cognito User Pool for dashboard authentication',
});

// Pipeline stack (Lambda durable function discover+assess + API Lambda) —
// solution adoption tracking lives here (main stack) only.
const pipelineStack = new PipelineStack(app, `LambdaRuntimeMigrationPipeline-${region}`, {
  env,
  table: dataStack.table,
  bucketName: dataStack.bucket.bucketName,
  spokeExternalId,
  description: 'AI Lambda Runtime Migration Pipeline: Lambda durable function discovering and assessing deprecated-runtime Lambda functions (uksb-do9bhieqqh)(tag:lambda-runtime-migration,operations-automation)',
});

// API stack (HTTP API + Cognito JWT authorizer over the pipeline/API Lambda)
const apiStack = new ApiStack(app, `LambdaRuntimeMigrationApi-${region}`, {
  env,
  apiFunction: pipelineStack.apiFunction,
  userPool: authStack.userPool,
  userPoolClient: authStack.userPoolClient,
  description: 'Lambda Runtime Migration: HTTP API for UI actions and refresh pipeline control',
});

// Frontend stack (S3 + CloudFront; calls the HTTP API with the Cognito JWT)
new FrontendStack(app, `LambdaRuntimeMigrationFrontend-${region}`, {
  env,
  apiUrl: apiStack.apiUrl,
  userPoolId: authStack.userPool.userPoolId,
  userPoolClientId: authStack.userPoolClient.userPoolClientId,
  description: 'Lambda Runtime Migration: CloudFront dashboard served from S3',
});

// Spoke stack (opt-in multi-account scan): ONE read-only role,
// deployed in a MEMBER account with that account's credentials, pointing at the
// hub:
//   npx cdk deploy LambdaRuntimeMigrationSpoke-<region> --context hubAccountId=<hub account id>
// Only instantiated when a hub account id is given, so the default
// single-account synth never depends on it. No tracking tag: the Pipeline stack
// is the demo's single tracked stack.
const spokeHubAccountId: string = app.node.tryGetContext('hubAccountId') || '';
if (spokeHubAccountId) {
  new SpokeStack(app, `LambdaRuntimeMigrationSpoke-${region}`, {
    env,
    hubAccountId: spokeHubAccountId,
    externalId: spokeExternalId,
    synthesizer: new cdk.BootstraplessSynthesizer(), // one IAM role, no assets: no `cdk bootstrap` in the spoke
    description: 'Lambda Runtime Migration Spoke: read-only scan role assumed by the hub account',
  });
}

// Org stack (opt-in multi-account scan): StackSet rolling the spoke
// role out to every account of the organization root / OUs, deployed FROM the
// hub. Only instantiated when targets are given, so single-account users never
// see it:
//   npx cdk deploy LambdaRuntimeMigrationOrg-<region> --context orgTargets=r-xxxx[,ou-xxxx-yyyyyyyy]
// Requires StackSets trusted access and the hub to be the management account or
// a StackSets delegated administrator (shared/scripts/check-org-access).
const orgTargets: string = app.node.tryGetContext('orgTargets') || '';
if (orgTargets) {
  // hubAccountId context lets the management account run this stack on behalf of
  // a hub that is not a StackSets delegated administrator.
  new OrgStack(app, `LambdaRuntimeMigrationOrg-${region}`, {
    env,
    synthesizer: new cdk.BootstraplessSynthesizer(), // inline template, no assets
    hubAccountId: app.node.tryGetContext('hubAccountId') || process.env.CDK_DEFAULT_ACCOUNT || '',
    targetOuIds: orgTargets.split(',').map((s: string) => s.trim()).filter(Boolean),
    externalId: spokeExternalId,
    description: 'Lambda Runtime Migration Org: StackSet placing the read-only spoke role in every member account',
  });
}

// Optional mock fixture (self-contained test data: a CodeCommit repo + three
// deprecated-runtime Lambdas tagged for code-behind detection). Deployed by
// deploy-all -Mock / --mock or on its own with scripts/deploy-mock.*. No tracking tag:
// the Pipeline stack is the demo's single tracked stack. Only instantiated when
// --context mockFixture=true, so the default synth is unchanged.
const mockFixture = app.node.tryGetContext('mockFixture') === true
  || app.node.tryGetContext('mockFixture') === 'true';
if (mockFixture) {
  new MockFixtureStack(app, `LambdaRuntimeMigrationMock-${region}`, {
    env,
    description: 'Lambda Runtime Migration Mock Fixture: CodeCommit repo and three deprecated-runtime Lambda functions for code-behind detection testing',
  });
}

// Migration build plane (CodeBuild project + CodeBuild role + EventBridge rule
// running AWS Transform). Always deployed by deploy-all. The two migration
// Lambdas live in PipelineStack; TransformStack references them (and the Data
// bucket), so CDK infers Transform -> Pipeline / Data dependencies and orders
// deploy and `cdk destroy --all` accordingly. No tracking tag: the Pipeline
// stack is the demo's single tracked stack.
new TransformStack(app, `LambdaRuntimeMigrationTransform-${region}`, {
  env,
  bucketName: dataStack.bucket.bucketName,
  orchestratorFunction: pipelineStack.migrateFunction,
  buildCompleteFunction: pipelineStack.buildCompleteFunction,
  description: 'Lambda Runtime Migration Transform: CodeBuild project, build role and EventBridge rule running AWS Transform (atx) migrations',
});

app.synth();
