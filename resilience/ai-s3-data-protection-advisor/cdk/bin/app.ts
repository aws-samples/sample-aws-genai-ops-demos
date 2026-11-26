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
import { novaLiteModelId } from '../lib/model-id';
import { getRegion } from '../../../../shared/utils/aws-utils';

const app = new cdk.App();
const region = getRegion();
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };
const ctx = (key: string): string => `${app.node.tryGetContext(key) ?? ''}`.trim();
const list = (value: string): string[] => value.split(',').map((s) => s.trim()).filter(Boolean);

// Context keys, all optional (never in cdk.json). The default synth is the single-account hub.
//   multiAccount=true             hub may scan the organization (org reads + spoke assume-role, scope in the UI)
//   scanAccounts=<id>,<id>        hub scans these member accounts instead of listing the organization
//   orgTargets=r-...,ou-...       also roll the spoke role out with the Org stack (StackSet)
//   hubAccountId=<id>             the hub account, for the Org and Spoke stacks
//   hubRegion=<region>            the hub region, when it differs from the deploy region (spoke and org modes)
//   spoke=true                    synthesize only the Spoke stack (deploy it by hand in one member account)
//   orgOnly=true                  synthesize only the Org stack (a management account rolling out for a hub)
//   stackSetsCallAs=SELF|DELEGATED_ADMIN   who creates the StackSet: the management account (default) or a
//                                 StackSets delegated administrator
const hubAccountId = ctx('hubAccountId');
const callAs = ctx('stackSetsCallAs') || 'SELF';
if (callAs !== 'SELF' && callAs !== 'DELEGATED_ADMIN') {
  throw new Error(`stackSetsCallAs must be SELF or DELEGATED_ADMIN, got '${callAs}'`);
}
const hubRegion = ctx('hubRegion') || region;
const orgTargets = list(ctx('orgTargets'));

if (ctx('spoke') === 'true') {
  new SpokeStack(app, 'S3DataProtectionSpoke', {
    env,
    hubAccountId,
    hubRegion,
    synthesizer: new cdk.BootstraplessSynthesizer(), // member accounts need no CDK bootstrap
    description: 'S3 Data Protection Advisor: read-only role the hub assumes to scan this account',
  });
} else if (ctx('orgOnly') === 'true') {
  new OrgStack(app, `S3DataProtectionOrg-${region}`, {
    env,
    hubAccountId,
    hubRegion,
    targetOuIds: orgTargets,
    callAs,
    synthesizer: new cdk.BootstraplessSynthesizer(), // a management account needs no CDK bootstrap
    description: 'S3 Data Protection Advisor: StackSet rolling the spoke role out to member accounts',
  });
} else {
  // -c modelId=<inference profile id> overrides the Amazon Nova Lite profile (the IAM grant builds an
  // inference-profile ARN from it, so a plain foundation model id does not work); -c modelId=off (or none)
  // disables the AI steps.
  const modelContext = ctx('modelId') || undefined;
  const modelId = modelContext && ['off', 'none'].includes(modelContext.toLowerCase())
    ? ''
    : novaLiteModelId(region, modelContext);
  const scanAccounts = list(ctx('scanAccounts'));
  for (const id of scanAccounts) {
    if (!/^\d{12}$/.test(id)) throw new Error(`scanAccounts: '${id}' is not a 12-digit account id`);
  }
  const multiAccount = ctx('multiAccount') === 'true' || scanAccounts.length > 0 || orgTargets.length > 0;

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
    multiAccount,
    scanAccounts,
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
    multiAccount,
    description: 'S3 Data Protection Advisor: CloudFront dashboard served from S3',
  });

  // Only instantiated with orgTargets, so the default synth (and a teardown without it) never depends on it.
  if (orgTargets.length) {
    new OrgStack(app, `S3DataProtectionOrg-${region}`, {
      env,
      hubAccountId: hubAccountId || `${process.env.CDK_DEFAULT_ACCOUNT ?? ''}`,
      hubRegion: region,
      targetOuIds: orgTargets,
      callAs,
      synthesizer: new cdk.BootstraplessSynthesizer(),
      description: 'S3 Data Protection Advisor: StackSet rolling the spoke role out to member accounts',
    });
  }
}

app.synth();
