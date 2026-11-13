import { WaAgentGoal } from './wa-agent';

/**
 * Goals of the multi-region variant, one per pillar the agent analyzes
 * (Operational Excellence and Sustainability are not in scope for the agent).
 * They point at the gaps deliberately left in this workload.
 */
export const MULTI_REGION_AGENT_GOALS: WaAgentGoal[] = [
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
];
