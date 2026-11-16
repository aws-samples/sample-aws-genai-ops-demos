import { CfnOutput, Fn, Stack, StackProps, aws_iam as iam, aws_wellarchitected as wellarchitected } from 'aws-cdk-lib';
import { Construct } from 'constructs';

/*
 * Well-Architected Agent side of the demo, in two reusable stacks:
 *
 *  - WaAgentProfileStack: ONE profile per account and agent Region, shared by every
 *    application of the demo: execution role, access role, profile, goals.
 *  - WaApplicationContextStack: one per application deployment. It registers the
 *    application in the shared profile (an APPLICATION context scoped by the
 *    application's `project` tag and Regions). Adding an application to the demo
 *    means adding its workload stacks and one of these, nothing in the profile.
 *
 * Both live in the profile Region (WA_AGENT_REGION): a CloudFormation stack only
 * creates resources in its own Region, and a context belongs to its profile. The
 * workload stacks deploy wherever the user deploys and know nothing about the agent.
 *
 * Hosting Regions: a profile (preview) can only be hosted in a few Regions, and
 * scans resources in every commercial Region wherever it is hosted:
 * https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent-quotas.html
 * The list lives in ONE place, the shared prerequisites check
 * (shared/scripts/check-prerequisites, service "wellarchitected-agent"), which also
 * resolves WA_AGENT_REGION. This app only reads the resolved Region.
 */

/**
 * CloudFormation export of the profile ARN. Export names are per account and Region,
 * like the profile, so the name needs no suffix. Context stacks import it, which
 * also makes CloudFormation refuse to delete the profile stack while an
 * application is still registered.
 */
export const PROFILE_ARN_EXPORT = 'WellArchitectedAgentReviewProfileArn';

/** Profile name, display name and IAM role name prefix of the shared profile. */
const PROFILE_NAME = 'wa-agent-demo-profile';
const ROLE_NAME_PREFIX = 'WAAgent';

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

/**
 * Goals of the shared profile, one per pillar the agent analyzes. Goals belong to
 * the profile, not to an application, so they are written for every application
 * the demo deploys. A profile accepts at most 10 goals.
 */
export const PROFILE_GOALS: WaAgentGoal[] = [
  {
    pillar: 'RESILIENCE',
    title: 'Meet each application\'s declared RPO and RTO by removing single points of failure',
    description:
      'Spread every tier across Availability Zones and, where an application declares it, across Regions; back data up with point-in-time recovery; detect failures and fail over without manual action; and test recovery regularly.',
  },
  {
    pillar: 'SECURITY',
    title: 'Enforce least-privilege access and encrypt data at rest and in transit',
    description:
      'Replace broad permissions with scoped roles, encrypt every data store, require TLS for all access, keep data tiers out of public subnets, and protect internet-facing endpoints with access logs and a web application firewall.',
  },
  {
    pillar: 'COST_OPTIMIZATION',
    title: 'Match capacity to demand and remove idle or oversized resources',
    description:
      'Align instance types and capacity with observed utilization and scale in when traffic is low, without lowering the resilience targets.',
  },
  {
    pillar: 'PERFORMANCE',
    title: 'Scale automatically to sustain peak load without manual intervention',
    description:
      'Replace manually sized or fixed capacity with elastic, target-tracking scaling behind load balancers, so response times stay stable as traffic grows, including after a failover.',
  },
];

export interface WaAgentProfileStackProps extends StackProps {
  /**
   * Regions the profile scans: every enabled Region of the account (read by the
   * deploy scripts). Each application context narrows the scope to its own Regions
   * and `project` tag, so applications in any Region can join without a profile change.
   */
  scanRegions: string[];
}

/**
 * The shared agent profile. Configured correctly on purpose (no WA-ANTIPATTERN).
 * Single-account setup: the profile, its execution role and the access role all
 * live in this account.
 * https://docs.aws.amazon.com/wellarchitected/latest/userguide/security-iam-agent.html
 *
 * Fixed construct ids (AgentExecutionRole, AgentAccessRole, AgentProfile,
 * AgentGoal<Pillar>) keep logical IDs stable, so an update never replaces the profile.
 */
export class WaAgentProfileStack extends Stack {
  constructor(scope: Construct, id: string, props: WaAgentProfileStackProps) {
    super(scope, id, props);

    // ---------------------------------------------------------------------
    // Execution role: assumed by the agent service to orchestrate discovery.
    // Its only permission is sts:AssumeRole on the access role (granted below).
    // Trust policy (SEC 03, confused deputy): the same conditions as the role the
    // console creates. Only this account, and only for an agent profile of this
    // account. The profile ARN cannot be referenced here (the profile depends on
    // this role), so the source ARN matches any agent profile of the account, as
    // in the console's policy. Partition and account come from the stack.
    // IAM is global, so fixed role names carry the profile Region.
    // ---------------------------------------------------------------------
    const agentExecutionRole = new iam.Role(this, 'AgentExecutionRole', {
      roleName: `${ROLE_NAME_PREFIX}ExecutionRole-${this.region}`,
      description: 'Assumed by the Well-Architected Agent to orchestrate resource discovery',
      assumedBy: new iam.ServicePrincipal('wellarchitected.amazonaws.com').withConditions({
        StringEquals: { 'aws:SourceAccount': this.account },
        ArnLike: {
          'aws:SourceArn': this.formatArn({
            service: 'wellarchitected',
            region: '*',
            resource: 'agent-profile',
            resourceName: '*',
          }),
        },
      }),
    });

    // ---------------------------------------------------------------------
    // Access role: assumed by the execution role, read-only access to resources.
    // WellArchitectedAgentResourceScanning is the AWS managed policy the service
    // documents for this role: Get/List/Describe actions only, on Resource "*"
    // because discovery has to read resources it does not know in advance.
    // ---------------------------------------------------------------------
    const agentAccessRole = new iam.Role(this, 'AgentAccessRole', {
      roleName: `${ROLE_NAME_PREFIX}AccessRole-${this.region}`,
      description: 'Read-only access for the Well-Architected Agent to analyze this account',
      // Trust the execution role ARN, not the account root (documented best practice).
      assumedBy: new iam.ArnPrincipal(agentExecutionRole.roleArn),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('WellArchitectedAgentResourceScanning')],
    });
    // Adds sts:AssumeRole on exactly this role to the execution role's policy.
    agentAccessRole.grantAssumeRole(agentExecutionRole);

    // ---------------------------------------------------------------------
    // Agent profile
    // The roles never reference the profile, so there is no circular dependency:
    // execution role -> access role -> profile -> goals (here) / contexts (elsewhere).
    // ---------------------------------------------------------------------
    const agentProfile = new wellarchitected.CfnAgentProfile(this, 'AgentProfile', {
      name: PROFILE_NAME,
      // "WA Agent" is the short form the service docs use; "Profile", not "Review",
      // so it is not confused with the console's architecture Reviews.
      displayName: 'WA Agent Demo Profile',
      description: 'Reviews the applications of the Well-Architected Agent Review demo',
      pillars: PROFILE_GOALS.map((g) => g.pillar),
      executionRoleArn: agentExecutionRole.roleArn,
      aggregationConfiguration: [
        {
          accountId: this.account,
          regions: props.scanRegions,
          accessRoleArn: agentAccessRole.roleArn,
        },
      ],
      // Termination protection off, otherwise `cdk destroy` cannot delete it.
      deletionProtection: false,
    });
    // Depend on the whole role constructs, so the execution role's policy (with
    // sts:AssumeRole) exists before the service validates the profile.
    agentProfile.node.addDependency(agentExecutionRole, agentAccessRole);

    // ---------------------------------------------------------------------
    // Goals: at least one is required for prioritized recommendations.
    // ---------------------------------------------------------------------
    PROFILE_GOALS.forEach((goal) => {
      const id = goal.pillar
        .toLowerCase()
        .split('_')
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join('');
      new wellarchitected.CfnAgentGoal(this, `AgentGoal${id}`, {
        profileArn: agentProfile.attrArn,
        pillars: [goal.pillar],
        title: goal.title,
        description: goal.description,
      });
    });

    new CfnOutput(this, 'AgentProfileArn', {
      value: agentProfile.attrArn,
      description: 'Shared Well-Architected Agent profile of the demo',
      exportName: PROFILE_ARN_EXPORT,
    });
  }
}

/** What an application tells the agent about itself. */
export interface WaApplicationContext {
  /** Value of the `project` tag on every resource of the application (the scope). */
  projectTag: string;
  /** Regions the application runs in (the scope, within the profile's scan Regions). */
  regions: string[];
  title: string;
  applicationOverview: string;
  architectureOverview: string;
  /**
   * Declared requirements. The demo applications declare more than they deliver on
   * purpose, so the agent can flag the gap.
   */
  additionalContext: string;
}

export interface WaApplicationContextStackProps extends StackProps {
  context: WaApplicationContext;
}

/**
 * Registers one application in the shared profile: an APPLICATION context. A
 * profile without any context is invalid and runs no scheduled recommendations,
 * so at least one application must be deployed. Must be deployed in the profile
 * Region, after the profile stack (it imports PROFILE_ARN_EXPORT).
 */
export class WaApplicationContextStack extends Stack {
  constructor(scope: Construct, id: string, props: WaApplicationContextStackProps) {
    super(scope, id, props);
    const c = props.context;
    new wellarchitected.CfnAgentContext(this, 'AgentApplicationContext', {
      profileArn: Fn.importValue(PROFILE_ARN_EXPORT),
      title: c.title,
      contextType: 'APPLICATION',
      content: {
        criticality: 'BUSINESS_CRITICAL',
        applicationType: 'OTHER',
        applicationOverview: c.applicationOverview,
        architectureOverview: c.architectureOverview,
        additionalContext: c.additionalContext,
        resourceTags: [{ key: 'project', value: c.projectTag }],
        accountIds: [this.account],
        regions: c.regions,
      },
    });
  }
}
