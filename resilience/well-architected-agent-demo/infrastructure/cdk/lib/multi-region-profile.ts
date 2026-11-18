import { AgentProfileSpec } from './agent-profile-stack';
import { MultiRegionConfig } from './multi-region-config';

/**
 * Agent profile of the `multi` variant: scans the primary and secondary Regions,
 * goals that point at the gaps deliberately left in this workload, and one
 * application context scoped by the variant's `project` tag. The declared
 * requirements go beyond what is deployed on purpose (no automatic Regional
 * failover, no alarms), so the agent can flag the gap.
 */
export function multiRegionProfile(cfg: MultiRegionConfig): AgentProfileSpec {
  return {
    variant: 'multi',
    displayName: 'WA Agent Demo Profile (multi-region)',
    description: 'Reviews the multi-region active/active workload of the Well-Architected Agent demo',
    regions: [cfg.primaryRegion, cfg.secondaryRegion],
    workloadRegion: cfg.primaryRegion,
    secondaryRegion: cfg.secondaryRegion,
    goals: [
      {
        pillar: 'RESILIENCE',
        title: 'Fail over between Regions automatically within the declared RTO of 15 minutes',
        description:
          'Route users to both Regions with health-checked DNS so that a Regional impairment is detected and traffic moves without manual action, and alarm on failures so that recovery starts within minutes.',
      },
      {
        pillar: 'SECURITY',
        title: 'Protect the internet-facing endpoints and encrypt all traffic in transit',
        description:
          'Serve the application only over HTTPS, filter requests at the edge with a web application firewall, and keep access logs for every load balancer.',
      },
      {
        pillar: 'COST_OPTIMIZATION',
        title: 'Match web tier capacity to demand in both Regions',
        description:
          'Scale in when traffic is low instead of running fixed capacity around the clock, without lowering the resilience targets.',
      },
      {
        pillar: 'PERFORMANCE',
        title: 'Scale the web tier automatically to sustain peak load in each Region',
        description:
          'Replace fixed capacity with target-tracking scaling so that response times stay stable as traffic grows, including when one Region absorbs the traffic of the other.',
      },
    ],
    context: {
      projectTag: cfg.projectTag,
      title: 'Multi-region active/active web application',
      criticality: 'BUSINESS_CRITICAL',
      applicationType: 'OTHER',
      applicationOverview:
        'Customer-facing web application served from two AWS Regions, with a visit counter in a DynamoDB global table.',
      architectureOverview:
        'In each Region: internet-facing Application Load Balancer, multi-AZ Auto Scaling group in private subnets, VPC endpoints, no NAT gateway. DynamoDB global table replicated between the two Regions.',
      additionalContext:
        'Business critical, RPO 1 minute, RTO 15 minutes, active/active in two Regions with automatic Regional failover, failures detected and alerted within 5 minutes',
    },
  };
}
