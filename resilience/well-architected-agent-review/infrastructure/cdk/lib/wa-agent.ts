import { CfnOutput, Stack, aws_iam as iam, aws_wellarchitected as wellarchitected } from 'aws-cdk-lib';

/**
 * Regions that can host a Well-Architected Agent profile (preview). A profile can
 * analyze resources in any commercial Region, but must itself live in one of these.
 * https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent-quotas.html
 */
export const WA_AGENT_PROFILE_REGIONS = ['us-east-1', 'us-east-2', 'us-west-2'];

/**
 * One agent goal. Pillars the agent analyzes: RESILIENCE, SECURITY,
 * COST_OPTIMIZATION, PERFORMANCE. Operational Excellence and Sustainability are
 * not in scope for the agent, even though the API enum lists OPERATIONAL_EXCELLENCE.
 */
export interface WaAgentGoal {
  pillar: string;
  title: string;
  description: string;
}

export interface WaAgentProps {
  /** Profile name; also the value of the `project` tag that scopes the context. */
  projectTag: string;
  /** Prefix of the two IAM role names (IAM is global, so the Region is appended). */
  roleNamePrefix: string;
  displayName: string;
  description: string;
  /** Regions the profile analyzes (the hosting Region must be one of them). */
  regions: string[];
  goals: WaAgentGoal[];
  context: {
    title: string;
    applicationOverview: string;
    architectureOverview: string;
    additionalContext: string;
  };
}

/** Fail the synth early if this stack's Region cannot host an agent profile. */
export function assertAgentProfileRegion(region: string): void {
  if (!WA_AGENT_PROFILE_REGIONS.includes(region)) {
    throw new Error(
      `Well-Architected Agent profiles can only be hosted in ${WA_AGENT_PROFILE_REGIONS.join(', ')}; ` +
        `this stack resolved region "${region}". Set AWS_REGION and AWS_DEFAULT_REGION to a supported Region.`,
    );
  }
}

/**
 * Well-Architected Agent side of the demo, shared by both variants. Configured
 * correctly on purpose (no WA-ANTIPATTERN). Single-account setup: the profile,
 * its execution role and the access role all live in this account.
 * https://docs.aws.amazon.com/wellarchitected/latest/userguide/security-iam-agent.html
 *
 * Resources are created directly on the stack with fixed construct ids
 * (AgentExecutionRole, AgentAccessRole, AgentProfile, AgentGoal<Pillar>,
 * AgentApplicationContext), so their logical IDs stay stable across refactors and
 * an update never replaces a deployed profile.
 *
 * @returns the agent profile ARN
 */
export function addWellArchitectedAgent(stack: Stack, props: WaAgentProps): string {
  // -----------------------------------------------------------------------
  // Execution role: assumed by the agent service to orchestrate discovery.
  // Its only permission is sts:AssumeRole on the access role (granted below).
  // SEC 03 exception: the trust policy names the exact service principal but
  // carries no aws:SourceAccount / aws:SourceArn condition. The service's
  // documentation does not say it sends those keys, and adding a condition the
  // service does not satisfy would break the agent silently. The documented
  // confused-deputy control is the profile ARN, used by the service as the
  // external ID in the role chain. Revisit once the official trust policy
  // created by the console has been checked.
  // IAM is global, so fixed role names carry the Region suffix.
  // -----------------------------------------------------------------------
  const agentExecutionRole = new iam.Role(stack, 'AgentExecutionRole', {
    roleName: `${props.roleNamePrefix}ExecutionRole-${stack.region}`,
    description: 'Assumed by the Well-Architected Agent to orchestrate resource discovery',
    assumedBy: new iam.ServicePrincipal('wellarchitected.amazonaws.com'),
  });

  // -----------------------------------------------------------------------
  // Access role: assumed by the execution role, read-only access to resources.
  // WellArchitectedAgentResourceScanning is the AWS managed policy the service
  // documents for this role: Get/List/Describe actions only, on Resource "*"
  // because discovery has to read resources it does not know in advance.
  // -----------------------------------------------------------------------
  const agentAccessRole = new iam.Role(stack, 'AgentAccessRole', {
    roleName: `${props.roleNamePrefix}AccessRole-${stack.region}`,
    description: 'Read-only access for the Well-Architected Agent to analyze this account',
    // Trust the execution role ARN, not the account root (documented best practice).
    assumedBy: new iam.ArnPrincipal(agentExecutionRole.roleArn),
    managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('WellArchitectedAgentResourceScanning')],
  });
  // Adds sts:AssumeRole on exactly this role to the execution role's policy.
  agentAccessRole.grantAssumeRole(agentExecutionRole);

  // -----------------------------------------------------------------------
  // Agent profile
  // The roles never reference the profile, so there is no circular dependency:
  // execution role -> access role -> profile -> goals / context.
  // -----------------------------------------------------------------------
  const agentProfile = new wellarchitected.CfnAgentProfile(stack, 'AgentProfile', {
    name: props.projectTag,
    displayName: props.displayName,
    description: props.description,
    pillars: props.goals.map((g) => g.pillar),
    executionRoleArn: agentExecutionRole.roleArn,
    aggregationConfiguration: [
      {
        accountId: stack.account,
        regions: props.regions,
        accessRoleArn: agentAccessRole.roleArn,
      },
    ],
    // Termination protection off, otherwise `cdk destroy` cannot delete it.
    deletionProtection: false,
  });
  // Depend on the whole role constructs, so the execution role's policy (with
  // sts:AssumeRole) exists before the service validates the profile.
  agentProfile.node.addDependency(agentExecutionRole, agentAccessRole);

  // -----------------------------------------------------------------------
  // Goals: at least one is required for prioritized recommendations.
  // One goal per pillar, so results map cleanly to the known anti-patterns.
  // -----------------------------------------------------------------------
  props.goals.forEach((goal) => {
    const id = goal.pillar
      .toLowerCase()
      .split('_')
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join('');
    new wellarchitected.CfnAgentGoal(stack, `AgentGoal${id}`, {
      profileArn: agentProfile.attrArn,
      pillars: [goal.pillar],
      title: goal.title,
      description: goal.description,
    });
  });

  // -----------------------------------------------------------------------
  // Application context: at least one is required, otherwise the profile is
  // invalid and scheduled recommendations do not run. The declared requirements
  // intentionally contradict what is deployed, so the agent can flag the gap
  // between the stated objectives and the actual architecture.
  // -----------------------------------------------------------------------
  new wellarchitected.CfnAgentContext(stack, 'AgentApplicationContext', {
    profileArn: agentProfile.attrArn,
    title: props.context.title,
    contextType: 'APPLICATION',
    content: {
      criticality: 'BUSINESS_CRITICAL',
      applicationType: 'OTHER',
      applicationOverview: props.context.applicationOverview,
      architectureOverview: props.context.architectureOverview,
      additionalContext: props.context.additionalContext,
      resourceTags: [{ key: 'project', value: props.projectTag }],
      accountIds: [stack.account],
      regions: props.regions,
    },
  });

  new CfnOutput(stack, 'AgentProfileArn', {
    value: agentProfile.attrArn,
    description: 'Well-Architected Agent profile that reviews this workload',
  });
  return agentProfile.attrArn;
}
