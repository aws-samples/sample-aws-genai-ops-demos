#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AgentProfileStack, Variant } from '../lib/agent-profile-stack';
import { resolveAgentRegion, resolvePrimaryRegion } from '../lib/regions';
import { SingleRegionWorkloadStack } from '../lib/single-region-workload-stack';
import { singleRegionProfile } from '../lib/single-region-profile';
import { loadConfig } from '../lib/multi-region-config';
import { MultiRegionWorkloadStack } from '../lib/multi-region-workload-stack';
import { multiRegionProfile } from '../lib/multi-region-profile';

/**
 * One CDK app, one variant per deployment, two blocks:
 *
 *  - Workload: the architecture under test, in the user's Region (and the
 *    secondary Region for `multi`). It contains nothing of the agent.
 *  - Agent: WAAgentDemoProfile-<agent-region>, in WA_AGENT_REGION: roles, profile,
 *    goals and one application context, all from the variant's profile spec. Main
 *    stack of every deployment: the only one with adoption tracking.
 *
 * The context finds the workload by its `project` tag and Region strings, so there
 * is no cross-stack reference. A new variant = <variant>-workload-stack.ts +
 * <variant>-profile.ts and one more branch here.
 *
 * Inputs come from the environment, set by the deploy scripts (they pass no
 * --context on destroy, and switch AWS_REGION between Regions):
 *   WA_DEMO_VARIANT           single | multi (or -c variant=...)
 *   WA_DEMO_PRIMARY_REGION    workload Region (default: the shared getRegion())
 *   WA_AGENT_REGION           agent Region (default: the workload Region)
 *   WA_DEMO_SECONDARY_REGION  secondary Region of `multi`
 */
const app = new cdk.App();
const variant = (process.env.WA_DEMO_VARIANT || app.node.tryGetContext('variant') || 'single') as Variant;
if (variant !== 'single' && variant !== 'multi') {
  throw new Error(`Unknown variant "${variant}". Use "single" or "multi".`);
}

const workloadRegion = resolvePrimaryRegion();
const agentRegion = resolveAgentRegion(workloadRegion);
const commonTags: Record<string, string> = { ManagedBy: 'cdk', Env: 'demo' };

let spec;
if (variant === 'single') {
  spec = singleRegionProfile(workloadRegion);
  new SingleRegionWorkloadStack(app, `WAAgentDemoSingleWorkload-${workloadRegion}`, {
    env: { region: workloadRegion },
    description: 'Well-Architected Agent demo: intentionally non-optimized single-region workload',
    projectTag: spec.context.projectTag,
  });
} else {
  // Secondary Region from -c secondaryRegion or WA_DEMO_SECONDARY_REGION (chosen
  // by the deploy scripts).
  const cfg = loadConfig(app);
  spec = multiRegionProfile(cfg);

  // The workload stacks look up AZs, the Route 53 health checker prefix list and
  // (with HTTPS) the public hosted zone at synth time, so the account must be
  // known. It comes from the active credentials, never from configuration.
  const account = process.env.CDK_DEFAULT_ACCOUNT;
  if (!account) {
    throw new Error('CDK_DEFAULT_ACCOUNT is not set. Run cdk with valid AWS credentials.');
  }

  const primary = new MultiRegionWorkloadStack(app, `WAAgentDemoMultiWorkloadPrimary-${cfg.primaryRegion}`, {
    env: { account, region: cfg.primaryRegion },
    description: 'Well-Architected Agent demo: multi-region active/active workload, primary Region and DynamoDB global table',
    cfg,
    roleLabel: 'primary',
    vpcCidr: cfg.primaryVpcCidr,
  });
  const secondary = new MultiRegionWorkloadStack(app, `WAAgentDemoMultiWorkloadSecondary-${cfg.secondaryRegion}`, {
    env: { account, region: cfg.secondaryRegion },
    description: 'Well-Architected Agent demo: multi-region active/active workload, secondary Region',
    cfg,
    roleLabel: 'secondary',
    vpcCidr: cfg.secondaryVpcCidr,
  });
  // Deploy the secondary after the primary: the primary creates the DynamoDB
  // replica in the secondary Region and (with HTTPS) the shared ACM validation records.
  secondary.addStackDependency(primary);

  // `project` scopes the application context of the agent stack.
  for (const stack of [primary, secondary]) {
    cdk.Tags.of(stack).add('project', spec.context.projectTag);
    for (const [k, v] of Object.entries(commonTags)) cdk.Tags.of(stack).add(k, v);
  }
}

// Agent stack. Only the Region is pinned; the account comes from the active
// credentials. Solution adoption tracking lives here, in the app file, not in the
// stack class: see .kiro/steering/solution-adoption-tracking.md.
new AgentProfileStack(app, `WAAgentDemoProfile-${agentRegion}`, {
  env: { region: agentRegion },
  description: `Well-Architected Agent demo profile (${variant}) (uksb-do9bhieqqh)(tag:well-architected-agent-demo,resilience)`,
  spec,
});

app.synth();
