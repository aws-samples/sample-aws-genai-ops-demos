import { getRegion } from '../../../../../shared/utils/aws-utils';

/** AWS Region code, e.g. us-east-1, ap-southeast-2, us-gov-west-1. */
export const REGION_RE = /^[a-z]{2}(-[a-z]+)+-\d$/;

/**
 * Workload Region (the primary Region for `multi`): `WA_DEMO_PRIMARY_REGION` when
 * set (the deploy scripts pin it, because they switch AWS_REGION to bootstrap and
 * deploy the other Regions), otherwise the shared getRegion() resolution
 * (AWS_DEFAULT_REGION, AWS_REGION, CLI config). Any Region: only the agent
 * profile is limited to the hosting Regions.
 */
export function resolvePrimaryRegion(): string {
  return process.env.WA_DEMO_PRIMARY_REGION || getRegion();
}

/**
 * Region of the agent stack: `WA_AGENT_REGION` when set, otherwise the workload
 * Region. The deploy scripts get it from the shared prerequisites check
 * (`wellarchitected-agent`), which holds the hosting-Region list and stops in a
 * Region that cannot host a profile. A manual synth is not checked against that
 * list: CloudFormation rejects the profile instead.
 */
export function resolveAgentRegion(workloadRegion: string): string {
  const agentRegion = process.env.WA_AGENT_REGION || workloadRegion;
  if (!REGION_RE.test(agentRegion)) {
    throw new Error(`WA_AGENT_REGION must be an AWS Region code, got "${agentRegion}"`);
  }
  return agentRegion;
}
