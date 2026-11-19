import { AgentProfileSpec } from './agent-profile-stack';

/** Tag that marks every resource of the `single` workload for the agent's context. */
export const SINGLE_REGION_PROJECT_TAG = 'wa-agent-demo-single';

/**
 * Agent profile of the `single` variant: scans the workload Region only, goals
 * aimed at the anti-patterns of this workload, and one application context. The
 * declared requirements intentionally contradict what is deployed, so the agent
 * can flag the gap between the stated objectives and the actual architecture.
 */
export function singleRegionProfile(workloadRegion: string): AgentProfileSpec {
  return {
    variant: 'single',
    displayName: 'WA Agent Demo Profile (single-region)',
    description: 'Reviews the single-region workload of the Well-Architected Agent demo',
    regions: [workloadRegion],
    workloadRegion,
    goals: [
      {
        pillar: 'RESILIENCE',
        title: 'Meet the declared RPO of 5 minutes and RTO of 2 hours by removing single points of failure',
        description:
          'Run the database and the application tier across at least two Availability Zones, enable automated backups with point-in-time recovery, and regularly test recovery so that the declared RPO and RTO are achievable.',
      },
      {
        pillar: 'SECURITY',
        title: 'Enforce least-privilege access and encryption of all workload data at rest and in transit',
        description:
          'Replace broad administrative permissions with scoped roles, encrypt every data store, require TLS for all data access, and keep the data tier out of public subnets.',
      },
      {
        pillar: 'COST_OPTIMIZATION',
        title: 'Reduce compute spend by at least 30% by rightsizing over-provisioned resources',
        description:
          'Align instance types and capacity with observed utilization, and remove idle or oversized capacity without lowering the resilience targets.',
      },
      {
        pillar: 'PERFORMANCE',
        title: 'Scale the web tier automatically to sustain peak load without manual intervention',
        description:
          'Replace manually sized capacity with elastic scaling behind a load balancer, so that response times stay stable as traffic grows.',
      },
    ],
    context: {
      projectTag: SINGLE_REGION_PROJECT_TAG,
      title: 'Well-Architected Agent demo web application',
      criticality: 'BUSINESS_CRITICAL',
      applicationType: 'OTHER',
      applicationOverview: 'Customer-facing web application with a MySQL database and an S3 asset store.',
      architectureOverview:
        'Single EC2 web instance, single RDS MySQL instance, S3 asset bucket, credentials in Secrets Manager.',
      additionalContext: 'Business critical, RPO 5 minutes, RTO 2 hours, HA and DR in place',
    },
  };
}
