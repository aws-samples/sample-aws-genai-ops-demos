#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { getRegion } from '../../../../../shared/utils/aws-utils';

// Stack imports — Core
import { AuthStack } from '../lib/auth-stack';
import { DataStack } from '../lib/data-stack';

// Stack imports — InfraStacks
import { CostInfraStack } from '../lib/cost-infra-stack';
import { HealthInfraStack } from '../lib/health-infra-stack';
import { SupportInfraStack } from '../lib/support-infra-stack';
import { TAInfraStack } from '../lib/ta-infra-stack';
import { CURInfraStack } from '../lib/cur-infra-stack';
import { NetworkInfraStack } from '../lib/network-infra-stack';
import { OrchInfraStack } from '../lib/orch-infra-stack';

// Stack imports — RuntimeStacks
import { CostRuntimeStack } from '../lib/cost-runtime-stack';
import { HealthRuntimeStack } from '../lib/health-runtime-stack';
import { SupportRuntimeStack } from '../lib/support-runtime-stack';
import { TARuntimeStack } from '../lib/ta-runtime-stack';
import { CURRuntimeStack } from '../lib/cur-runtime-stack';
import { NetworkRuntimeStack } from '../lib/network-runtime-stack';
import { OrchRuntimeStack } from '../lib/orch-runtime-stack';

// Stack imports — Network Data (conditional)
import { NetworkDataStack } from '../lib/network-data-stack';

// Stack imports — Frontend
import { FrontendStack } from '../lib/frontend-stack';

// ---------------------------------------------------------------------------
// Region detection via shared utilities
// ---------------------------------------------------------------------------
const region = getRegion();
const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region,
};

const app = new cdk.App();

// ---------------------------------------------------------------------------
// Core Stacks (always deployed)
// ---------------------------------------------------------------------------
const authStack = new AuthStack(app, `GOATAuth-${region}`, { env });
const dataStack = new DataStack(app, `GOATData-${region}`, { env });

// ---------------------------------------------------------------------------
// Network Data resolution (Reqs 7.1–7.4, 10.3)
//
// The Network_Data_Bucket can be supplied in one of two ways:
//
//   1. Reuse path — the existing GOATData-${region} stack exports
//      `GOATSharedDataBucketName`. The deployment scripts perform the
//      `aws cloudformation list-exports` lookup (with the documented
//      10-second timeout) and pass the result to CDK via the context key
//      `goatSharedDataBucketName`. When this context value is non-empty,
//      no NetworkDataStack is instantiated and NetworkInfraStack imports
//      the bucket via `cdk.Fn.importValue('GOATSharedDataBucketName')`.
//
//   2. Dedicated path — the export is absent. The CDK app instantiates
//      `GOATNetworkData-${region}` which provisions a dedicated bucket
//      with the `raw/`/`parquet/` lifecycle rules, and passes the
//      bucket name into NetworkInfraStack via the
//      `networkDataBucketName` prop.
//
// CDK does not support synchronous in-process CFN export lookups during
// synthesis (`cdk.Fn.importValue` only produces a deploy-time token).
// The deployment scripts perform the lookup out-of-band and inject the
// result via context, which is the standard CDK pattern for "did this
// upstream resource exist before synth?" decisions.
// ---------------------------------------------------------------------------
const sharedDataBucketContext = app.node.tryGetContext('goatSharedDataBucketName');
const sharedDataBucketName: string | undefined =
  typeof sharedDataBucketContext === 'string' && sharedDataBucketContext.trim().length > 0
    ? sharedDataBucketContext.trim()
    : undefined;

let networkDataStack: NetworkDataStack | undefined;
let resolvedNetworkDataBucketName: string | undefined;

if (sharedDataBucketName === undefined) {
  // Dedicated path — no shared bucket; provision NetworkDataStack.
  networkDataStack = new NetworkDataStack(app, `GOATNetworkData-${region}`, { env });
  resolvedNetworkDataBucketName = networkDataStack.networkDataBucket.bucketName;
} else {
  // Reuse path — leave undefined so NetworkInfraStack uses Fn.importValue
  // on the GOATSharedDataBucketName CFN export.
  resolvedNetworkDataBucketName = undefined;
}

// ---------------------------------------------------------------------------
// Infrastructure Stacks (create ECR, CodeBuild, S3, IAM — export via CfnOutput)
// ---------------------------------------------------------------------------
const costInfra = new CostInfraStack(app, `GOATCostInfra-${region}`, { env });
const healthInfra = new HealthInfraStack(app, `GOATHealthInfra-${region}`, { env });
const supportInfra = new SupportInfraStack(app, `GOATSupportInfra-${region}`, { env });
const taInfra = new TAInfraStack(app, `GOATTAInfra-${region}`, { env });
const curInfra = new CURInfraStack(app, `GOATCURInfra-${region}`, { env });
const networkInfra = new NetworkInfraStack(app, `GOATNetworkInfra-${region}`, {
  env,
  networkDataBucketName: resolvedNetworkDataBucketName,
  // "Bring Your Own VPC" context values — when set, the collector deploys
  // into an existing customer VPC/subnet instead of creating a dedicated one.
  // Pass via: npx cdk deploy -c goatExistingVpcId=vpc-xxx -c goatCollectorSubnetIds=subnet-aaa,subnet-bbb
  existingVpcId: (() => {
    const v = app.node.tryGetContext('goatExistingVpcId');
    return typeof v === 'string' && v.trim() ? v.trim() : undefined;
  })(),
  collectorSubnetIds: (() => {
    const v = app.node.tryGetContext('goatCollectorSubnetIds');
    if (typeof v === 'string' && v.trim()) {
      return v.trim().split(',').map((s: string) => s.trim()).filter((s: string) => s);
    }
    return undefined;
  })(),
  vpcCidr: (() => {
    const v = app.node.tryGetContext('goatVpcCidr');
    return typeof v === 'string' && v.trim() ? v.trim() : undefined;
  })(),
  skipVpcEndpoints: (() => {
    const v = app.node.tryGetContext('goatSkipVpcEndpoints');
    return v === 'true' || v === true ? true : undefined;
  })(),
  collectorInstanceType: (() => {
    const v = app.node.tryGetContext('goatCollectorInstanceType');
    return typeof v === 'string' && v.trim() ? v.trim() : undefined;
  })(),
  collectorVolumeGib: (() => {
    const v = app.node.tryGetContext('goatCollectorVolumeGib');
    return typeof v === 'string' && v.trim() ? parseInt(v.trim(), 10) : undefined;
  })(),
});
if (networkDataStack !== undefined) {
  networkInfra.addDependency(networkDataStack);
}
const orchInfra = new OrchInfraStack(app, `GOATOrchInfra-${region}`, {
  env,
  // Capture_Conversation_Context persistence (Task 36, Reqs 9.20 /
  // 17.9). The orchestration agent needs DynamoDB read/write
  // permissions on the Conversations table provisioned by
  // ``DataStack`` so it can remember the most recently created
  // capture_id per conversation.
  conversationsTableArn: dataStack.conversationsTable.tableArn,
});
orchInfra.addDependency(dataStack);

// ---------------------------------------------------------------------------
// Runtime Stacks (import via Fn.importValue, upload source, trigger build,
// create AgentCore CfnRuntime)
// ---------------------------------------------------------------------------
const costRuntime = new CostRuntimeStack(app, `GOATCostRuntime-${region}`, { env });
costRuntime.addDependency(costInfra);

const healthRuntime = new HealthRuntimeStack(app, `GOATHealthRuntime-${region}`, { env });
healthRuntime.addDependency(healthInfra);

const supportRuntime = new SupportRuntimeStack(app, `GOATSupportRuntime-${region}`, { env });
supportRuntime.addDependency(supportInfra);

const taRuntime = new TARuntimeStack(app, `GOATTARuntime-${region}`, { env });
taRuntime.addDependency(taInfra);

const curRuntime = new CURRuntimeStack(app, `GOATCURRuntime-${region}`, { env });
curRuntime.addDependency(curInfra);

const networkRuntime = new NetworkRuntimeStack(app, `GOATNetworkRuntime-${region}`, { env });
networkRuntime.addDependency(networkInfra);

// ---------------------------------------------------------------------------
// Attach-by-Import resolution for the Network Agent (Reqs 4.5–4.9)
//
// By default (`--mode full`), the orchestrator resolves the Network Agent's
// runtime ARN via a direct in-app construct reference to `networkRuntime`,
// deployed in the same `cdk deploy` invocation. When
// `goatAttachNetworkByImport` is set to the string `"true"`, the orchestrator
// instead resolves the ARN via `cdk.Fn.importValue('GOATNetworkAgentRuntimeArn')`,
// the same mechanism `GOATDevOpsIntegrationStack` already uses unconditionally.
// This lets `OrchRuntimeStack` attach to a Network Agent stack that was
// deployed independently via `--mode network` or `--mode network-mcp`,
// without requiring `networkRuntime` to be part of this synthesis's
// dependency graph.
//
// Pass via: npx cdk deploy -c goatAttachNetworkByImport=true
// ---------------------------------------------------------------------------
const attachNetworkByImport = app.node.tryGetContext('goatAttachNetworkByImport') === 'true';
const resolvedNetworkAgentArn = attachNetworkByImport
  ? cdk.Fn.importValue('GOATNetworkAgentRuntimeArn')
  : networkRuntime.agentRuntimeArn;

// Orchestration runtime — receives sub-agent ARNs as environment variables.
// Solution adoption tracking goes ONLY on this stack (Req 15.5 / 10.7).
const orchRuntime = new OrchRuntimeStack(app, `GOATOrchRuntime-${region}`, {
  env,
  description: 'G.O.A.T. - Multi-agent orchestration for AWS operations analytics (uksb-do9bhieqqh)(tag:goat,operations-automation)',
  subAgentArns: {
    cost: costRuntime.agentRuntimeArn,
    health: healthRuntime.agentRuntimeArn,
    support: supportRuntime.agentRuntimeArn,
    ta: taRuntime.agentRuntimeArn,
    cur: curRuntime.agentRuntimeArn,
    network: resolvedNetworkAgentArn,
  },
  // Capture_Conversation_Context persistence (Task 36, Reqs 9.20 /
  // 17.9). Surface the Conversations table name into the
  // orchestration agent container as ``CONVERSATIONS_TABLE_NAME``
  // so ``state.py`` can target the same table the frontend uses
  // for chat transcripts (we co-locate by sort-key prefix).
  conversationsTableName: dataStack.conversationsTable.tableName,
});
orchRuntime.addDependency(orchInfra);
orchRuntime.addDependency(dataStack);
orchRuntime.addDependency(costRuntime);
orchRuntime.addDependency(healthRuntime);
orchRuntime.addDependency(supportRuntime);
orchRuntime.addDependency(taRuntime);
orchRuntime.addDependency(curRuntime);
if (!attachNetworkByImport) {
  orchRuntime.addDependency(networkRuntime);
}

// ---------------------------------------------------------------------------
// Frontend Stack
// ---------------------------------------------------------------------------
const frontendStack = new FrontendStack(app, `GOATFrontend-${region}`, {
  env,
  orchestrationArn: orchRuntime.agentRuntimeArn,
  userPoolId: authStack.userPool.userPoolId,
  userPoolClientId: authStack.userPoolClient.userPoolClientId,
  identityPoolId: authStack.identityPool.ref,
  region,
});
frontendStack.addDependency(authStack);
frontendStack.addDependency(dataStack);
frontendStack.addDependency(orchRuntime);

app.synth();
