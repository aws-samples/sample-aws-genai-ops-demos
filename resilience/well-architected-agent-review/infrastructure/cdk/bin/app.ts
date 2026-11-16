#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { singleRegionApplicationContext, WellArchitectedAgentReviewStack } from '../lib/well-architected-agent-review-stack';
import { loadConfig, resolveAgentRegion, resolvePrimaryRegion, resolveScanRegions } from '../lib/config';
import { RegionStack } from '../lib/region-stack';
import { DashboardStack } from '../lib/dashboard-stack';
import { WaAgentProfileStack, WaApplicationContextStack } from '../lib/wa-agent';
import { multiRegionApplicationContext } from '../lib/multi-region-agent';

/**
 * One CDK app, three building blocks:
 *
 *  1. Agent profile (always in the app): WellArchitectedAgentReviewProfile-<agent-region>.
 *     One per account and agent Region, shared by every application: roles,
 *     profile, goals. Main stack of the demo: the only one with adoption tracking.
 *  2. Application `single`: its workload stack, plus a context stack that registers
 *     it in the profile.
 *  3. Application `multi`: its workload stacks (primary, secondary, dashboard), plus
 *     a context stack that registers it in the profile.
 *
 * Context stacks always live in the profile Region (a stack only creates resources
 * in its own Region) and import the profile ARN. Workload stacks deploy in the
 * user's Region and contain nothing of the agent. A new application = its workload
 * stacks + one WaApplicationContextStack, with no change to the profile.
 *
 * Inputs come from the environment, set by the deploy scripts (they pass no
 * --context on destroy, and switch AWS_REGION between Regions):
 *   WA_DEMO_VARIANT        profile | single | multi (or -c variant=...)
 *   WA_DEMO_PRIMARY_REGION workload Region (default: the shared getRegion())
 *   WA_AGENT_REGION        profile Region (default: the workload Region)
 *   WA_AGENT_SCAN_REGIONS  Regions the profile scans (default: the profile Region)
 *   WA_DEMO_SECONDARY_REGION  secondary Region of `multi`
 */
const app = new cdk.App();
const variant = process.env.WA_DEMO_VARIANT || app.node.tryGetContext('variant') || 'single';
if (!['profile', 'single', 'multi'].includes(variant)) {
  throw new Error(`Unknown variant "${variant}". Use "profile", "single" or "multi".`);
}

const workloadRegion = resolvePrimaryRegion();
const agentRegion = resolveAgentRegion(workloadRegion);

// Profile and context stacks pin only their Region; the account comes from the
// active credentials. Same env in every variant, so the profile template does not
// change with the application being deployed.
const agentEnv = { region: agentRegion };

// 1. Shared agent profile. Solution adoption tracking lives here, in the app file,
// not in the stack class: see .kiro/steering/solution-adoption-tracking.md.
new WaAgentProfileStack(app, `WellArchitectedAgentReviewProfile-${agentRegion}`, {
  env: agentEnv,
  description:
    'Well-Architected Agent profile shared by the demo applications (uksb-do9bhieqqh)(tag:well-architected-agent-review,resilience)',
  scanRegions: resolveScanRegions(app, agentRegion),
});

if (variant === 'single') {
  // 2. Application `single`. Stack IDs carry the workload Region, so the same
  // application can run in several Regions under one profile.
  new WellArchitectedAgentReviewStack(app, `WellArchitectedAgentReviewStack-${workloadRegion}`, {
    env: { region: workloadRegion },
    description: 'Well-Architected Agent Review: intentionally non-optimized single-region application',
  });
  new WaApplicationContextStack(app, `WellArchitectedAgentReviewSingleContext-${workloadRegion}`, {
    env: agentEnv,
    description: `Well-Architected Agent Review: registers the single-region application in ${workloadRegion} in the profile`,
    context: singleRegionApplicationContext(workloadRegion),
  });
} else if (variant === 'multi') {
  // 3. Application `multi`. Secondary Region from -c secondaryRegion or
  // WA_DEMO_SECONDARY_REGION (chosen by the deploy scripts).
  const cfg = loadConfig(app);

  // The stacks look up AZs, the Route 53 health checker prefix list and (with
  // HTTPS) the public hosted zone at synth time, so the account must be known.
  // It comes from the active credentials, never from configuration.
  const account = process.env.CDK_DEFAULT_ACCOUNT;
  if (!account) {
    throw new Error('CDK_DEFAULT_ACCOUNT is not set. Run cdk with valid AWS credentials.');
  }

  const primary = new RegionStack(app, `WellArchitectedAgentReviewMultiPrimary-${cfg.primaryRegion}`, {
    env: { account, region: cfg.primaryRegion },
    description: 'Well-Architected Agent Review: multi-region active/active application, primary Region and DynamoDB global table',
    cfg,
    roleLabel: 'primary',
    vpcCidr: cfg.primaryVpcCidr,
  });

  const secondary = new RegionStack(app, `WellArchitectedAgentReviewMultiSecondary-${cfg.secondaryRegion}`, {
    env: { account, region: cfg.secondaryRegion },
    description: 'Well-Architected Agent Review: multi-region active/active application, secondary Region',
    cfg,
    roleLabel: 'secondary',
    vpcCidr: cfg.secondaryVpcCidr,
  });
  // Deploy the secondary after the primary: the primary creates the DynamoDB
  // replica in the secondary region and (with HTTPS) the shared ACM validation records.
  secondary.addStackDependency(primary);

  // Dashboard: ALB and target group dimensions arrive as --context from the deploy
  // script (no cross-Region references).
  new DashboardStack(app, `WellArchitectedAgentReviewMultiDashboard-${cfg.primaryRegion}`, {
    env: { account, region: cfg.primaryRegion },
    description: 'Well-Architected Agent Review: multi-region application, CloudWatch traffic dashboard',
    cfg,
  });

  new WaApplicationContextStack(app, `WellArchitectedAgentReviewMultiContext-${cfg.primaryRegion}`, {
    env: agentEnv,
    description: `Well-Architected Agent Review: registers the multi-region application (${cfg.primaryRegion}, ${cfg.secondaryRegion}) in the profile`,
    context: multiRegionApplicationContext(cfg),
  });

  // Common tags of this application. `project` scopes its application context.
  for (const stack of [primary, secondary]) {
    cdk.Tags.of(stack).add('project', cfg.projectTag);
    cdk.Tags.of(stack).add('ManagedBy', 'cdk');
    cdk.Tags.of(stack).add('Env', 'demo');
  }
}

app.synth();
