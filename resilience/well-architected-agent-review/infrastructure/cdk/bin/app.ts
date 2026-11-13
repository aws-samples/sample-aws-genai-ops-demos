#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { WellArchitectedAgentReviewStack } from '../lib/well-architected-agent-review-stack';
import { getRegion } from '../../../../../shared/utils/aws-utils';
import { loadConfig } from '../lib/config';
import { RegionStack } from '../lib/region-stack';
import { DashboardStack } from '../lib/dashboard-stack';

/**
 * Two variants of the same demo, one CDK app:
 *  - single (default): deliberately non-optimized single-region workload
 *    (EC2 + RDS + S3), one stack.
 *  - multi: active/active workload in two Regions with a few documented gaps,
 *    three stacks (primary, secondary, dashboard).
 * Both create a Well-Architected Agent profile (lib/wa-agent.ts) with their own
 * name, roles and project tag, so the two variants can coexist in one account.
 *
 * The variant comes from WA_DEMO_VARIANT (set by the deploy scripts, and the only
 * way to reach `cdk destroy`, which receives no --context from the shared
 * deploy-cdk script) or from `-c variant=...`.
 */
const app = new cdk.App();
const variant = process.env.WA_DEMO_VARIANT || app.node.tryGetContext('variant') || 'single';

if (variant === 'single') {
  const region = getRegion();
  // Main (and only) stack. Solution adoption tracking lives here, in the app file,
  // not in the stack class: see .kiro/steering/solution-adoption-tracking.md.
  // The stack ID carries the region suffix so IDs never collide across regions.
  new WellArchitectedAgentReviewStack(app, `WellArchitectedAgentReviewStack-${region}`, {
    // Only the region is pinned; the account comes from the active credentials.
    env: { region },
    description:
      'Intentionally non-optimized architecture for Well-Architected Agent analysis (uksb-do9bhieqqh)(tag:well-architected-agent-review,resilience)',
  });
} else if (variant === 'multi') {
  // Primary Region from WA_DEMO_PRIMARY_REGION or the shared getRegion(); the
  // secondary Region is derived from it (or set with -c secondaryRegion=...).
  const cfg = loadConfig(app);

  // The stacks look up AZs, the Route 53 health checker prefix list and (with
  // HTTPS) the public hosted zone at synth time, so the account must be known.
  // It comes from the active credentials, never from configuration.
  const account = process.env.CDK_DEFAULT_ACCOUNT;
  if (!account) {
    throw new Error('CDK_DEFAULT_ACCOUNT is not set. Run cdk with valid AWS credentials.');
  }

  // Main stack of this variant: the only one carrying solution adoption tracking.
  const primary = new RegionStack(app, `WellArchitectedAgentReviewMultiPrimary-${cfg.primaryRegion}`, {
    env: { account, region: cfg.primaryRegion },
    description:
      'Multi-region active/active web app, primary region, DynamoDB global table and Well-Architected Agent profile (uksb-do9bhieqqh)(tag:well-architected-agent-review,resilience)',
    cfg,
    roleLabel: 'primary',
    vpcCidr: cfg.primaryVpcCidr,
  });

  const secondary = new RegionStack(app, `WellArchitectedAgentReviewMultiSecondary-${cfg.secondaryRegion}`, {
    env: { account, region: cfg.secondaryRegion },
    description: 'Multi-region active/active web app, secondary region',
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
    description: 'Multi-region active/active web app, CloudWatch traffic dashboard',
    cfg,
  });

  // Common tags of this variant. `project` scopes the agent's application context
  // and the optional Resilience Hub tag-based resource groups.
  for (const stack of [primary, secondary]) {
    cdk.Tags.of(stack).add('project', cfg.projectTag);
    cdk.Tags.of(stack).add('ManagedBy', 'cdk');
    cdk.Tags.of(stack).add('Env', 'demo');
  }
} else {
  throw new Error(`Unknown variant "${variant}". Use "single" or "multi".`);
}

app.synth();
