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
import { OrchInfraStack } from '../lib/orch-infra-stack';

// Stack imports — RuntimeStacks
import { CostRuntimeStack } from '../lib/cost-runtime-stack';
import { HealthRuntimeStack } from '../lib/health-runtime-stack';
import { SupportRuntimeStack } from '../lib/support-runtime-stack';
import { TARuntimeStack } from '../lib/ta-runtime-stack';
import { CURRuntimeStack } from '../lib/cur-runtime-stack';
import { OrchRuntimeStack } from '../lib/orch-runtime-stack';

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
// Infrastructure Stacks (create ECR, CodeBuild, S3, IAM — export via CfnOutput)
// ---------------------------------------------------------------------------
const costInfra = new CostInfraStack(app, `GOATCostInfra-${region}`, { env });
const healthInfra = new HealthInfraStack(app, `GOATHealthInfra-${region}`, { env });
const supportInfra = new SupportInfraStack(app, `GOATSupportInfra-${region}`, { env });
const taInfra = new TAInfraStack(app, `GOATTAInfra-${region}`, { env });
const curInfra = new CURInfraStack(app, `GOATCURInfra-${region}`, { env });
const orchInfra = new OrchInfraStack(app, `GOATOrchInfra-${region}`, { env });

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

// Orchestration runtime — receives sub-agent ARNs as environment variables.
// Solution adoption tracking goes ONLY on this stack.
const orchRuntime = new OrchRuntimeStack(app, `GOATOrchRuntime-${region}`, {
  env,
  description: 'G.O.A.T. - Multi-agent orchestration for AWS operations analytics (uksb-do9bhieqqh)(tag:goat,operations-automation)',
  subAgentArns: {
    cost: costRuntime.agentRuntimeArn,
    health: healthRuntime.agentRuntimeArn,
    support: supportRuntime.agentRuntimeArn,
    ta: taRuntime.agentRuntimeArn,
    cur: curRuntime.agentRuntimeArn,
  },
});
orchRuntime.addDependency(orchInfra);
orchRuntime.addDependency(costRuntime);
orchRuntime.addDependency(healthRuntime);
orchRuntime.addDependency(supportRuntime);
orchRuntime.addDependency(taRuntime);
orchRuntime.addDependency(curRuntime);

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
