import { AppConfig } from './config';
import { WaApplicationContext } from './wa-agent';

/**
 * How the multi-region application registers in the shared agent profile
 * (WaApplicationContextStack, deployed in the profile Region): both workload
 * Regions, scoped by the variant's `project` tag. The declared requirements go
 * beyond what is deployed on purpose (no automatic Regional failover, no alarms),
 * so the agent can flag the gap.
 */
export function multiRegionApplicationContext(cfg: AppConfig): WaApplicationContext {
  return {
    projectTag: cfg.projectTag,
    regions: [cfg.primaryRegion, cfg.secondaryRegion],
    title: 'Multi-region active/active web application',
    applicationOverview:
      'Customer-facing web application served from two AWS Regions, with a visit counter in a DynamoDB global table.',
    architectureOverview:
      'In each Region: internet-facing Application Load Balancer, multi-AZ Auto Scaling group in private subnets, VPC endpoints, no NAT gateway. DynamoDB global table replicated between the two Regions.',
    additionalContext:
      'Business critical, RPO 1 minute, RTO 15 minutes, active/active in two Regions with automatic Regional failover, failures detected and alerted within 5 minutes',
  };
}
