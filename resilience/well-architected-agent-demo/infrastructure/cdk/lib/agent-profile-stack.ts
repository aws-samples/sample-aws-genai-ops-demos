import { CfnOutput, Stack, StackProps, aws_iam as iam, aws_wellarchitected as wellarchitected } from 'aws-cdk-lib';
import { Construct } from 'constructs';

/*
 * The agent side of one deployment: execution role, access role, profile, goals and
 * one application context, all built from a profile spec. Each variant provides its
 * spec (single-region-profile.ts, multi-region-profile.ts), so this stack knows no
 * variant. A deployment is one variant, so it has exactly one profile.
 *
 * The stack lives in the agent Region (WA_AGENT_REGION). The application context
 * finds the workload by its `project` tag and Region strings only: there is no
 * cross-stack reference, and the workload stacks know nothing about the agent.
 *
 * Hosting Regions: a profile (preview) can only be hosted in a few Regions, and
 * scans resources in any commercial Region wherever it is hosted:
 * https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent-quotas.html
 * The list lives in ONE place, the shared prerequisites check
 * (shared/scripts/check-prerequisites, service "wellarchitected-agent"), which also
 * resolves WA_AGENT_REGION. This app only reads the resolved Region.
 */

/** Profile name: unique per account and Region, and the last component of its ARN. */
const PROFILE_NAME = 'wa-agent-demo-profile';

/** Pillars of the profile, the same for every variant: all four the agent analyzes. */
const PROFILE_PILLARS = ['RESILIENCE', 'SECURITY', 'COST_OPTIMIZATION', 'PERFORMANCE'];

/** Variants of the demo. One deployment is exactly one of them. */
export type Variant = 'single' | 'multi';

/**
 * One agent goal. Pillars the agent analyzes: RESILIENCE, SECURITY,
 * COST_OPTIMIZATION, PERFORMANCE. Operational Excellence and Sustainability are
 * not in scope for the agent, even though the API enum lists OPERATIONAL_EXCELLENCE.
 */
export interface AgentGoal {
  pillar: string;
  title: string;
  description: string;
}

/** What the application tells the agent about itself (one APPLICATION context). */
export interface ApplicationContextSpec {
  /** Value of the `project` tag on every resource of the workload (the scope). */
  projectTag: string;
  title: string;
  /** MISSION_CRITICAL | BUSINESS_CRITICAL | NON_CRITICAL | TEST_DEVELOPMENT */
  criticality: string;
  /** SAS | DESKTOP_APPLICATION | OTHER */
  applicationType: string;
  applicationOverview: string;
  architectureOverview: string;
  /**
   * Declared requirements. The demo workloads declare more than they deliver on
   * purpose, so the agent can flag the gap.
   */
  additionalContext: string;
}

/** Everything that differs between the variants on the agent side. */
export interface AgentProfileSpec {
  variant: Variant;
  displayName: string;
  description: string;
  /** Workload Regions: the profile scans them, and the context is scoped to them. */
  regions: string[];
  /** Region the workload is deployed in (the primary Region for `multi`). */
  workloadRegion: string;
  /** Secondary Region of `multi`. */
  secondaryRegion?: string;
  /** At most 10 goals per profile. */
  goals: AgentGoal[];
  context: ApplicationContextSpec;
}

export interface AgentProfileStackProps extends StackProps {
  spec: AgentProfileSpec;
}

/**
 * Configured correctly on purpose (no WA-ANTIPATTERN). Single-account setup: the
 * profile, its execution role and the access role all live in this account.
 * https://docs.aws.amazon.com/wellarchitected/latest/userguide/security-iam-agent.html
 *
 * Fixed construct ids (AgentExecutionRole, AgentAccessRole, AgentProfile,
 * AgentGoal<Pillar>, AgentApplicationContext) keep logical IDs stable, so an update
 * never replaces the profile.
 */
export class AgentProfileStack extends Stack {
  constructor(scope: Construct, id: string, props: AgentProfileStackProps) {
    super(scope, id, props);
    const { spec } = props;

    // ---------------------------------------------------------------------
    // Execution role: assumed by the agent service to orchestrate discovery.
    // Its only permission is sts:AssumeRole on the access role (granted below).
    // Trust policy (SEC 03, confused deputy): the same conditions as the role the
    // console creates. Only this account, and only for an agent profile of this
    // account. The profile ARN cannot be referenced here (the profile depends on
    // this role), so the source ARN matches any agent profile of the account, as
    // in the console's policy. Partition and account come from the stack.
    // No roleName: nothing derives the ARN from the name (the profile takes both
    // ARNs by reference), so CloudFormation names the roles and two deployments
    // never collide.
    // ---------------------------------------------------------------------
    const agentExecutionRole = new iam.Role(this, 'AgentExecutionRole', {
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
      description: 'Read-only access for the Well-Architected Agent to analyze this account',
      // Trust the execution role ARN, not the account root (documented best practice).
      assumedBy: new iam.ArnPrincipal(agentExecutionRole.roleArn),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('WellArchitectedAgentResourceScanning')],
    });
    // Adds sts:AssumeRole on exactly this role to the execution role's policy.
    agentAccessRole.grantAssumeRole(agentExecutionRole);

    // ---------------------------------------------------------------------
    // Agent profile: scans only the workload Regions.
    // The roles never reference the profile, so there is no circular dependency:
    // execution role -> access role -> profile -> goals and context.
    // ---------------------------------------------------------------------
    const agentProfile = new wellarchitected.CfnAgentProfile(this, 'AgentProfile', {
      name: PROFILE_NAME,
      displayName: spec.displayName,
      description: spec.description,
      pillars: PROFILE_PILLARS,
      executionRoleArn: agentExecutionRole.roleArn,
      aggregationConfiguration: [
        {
          accountId: this.account,
          regions: spec.regions,
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
    spec.goals.forEach((goal) => {
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

    // ---------------------------------------------------------------------
    // Application context: a profile without one is not eligible for scheduled
    // recommendations. It points at the workload by tag, account and Regions.
    // ---------------------------------------------------------------------
    const c = spec.context;
    new wellarchitected.CfnAgentContext(this, 'AgentApplicationContext', {
      profileArn: agentProfile.attrArn,
      title: c.title,
      contextType: 'APPLICATION',
      content: {
        criticality: c.criticality,
        applicationType: c.applicationType,
        applicationOverview: c.applicationOverview,
        architectureOverview: c.architectureOverview,
        additionalContext: c.additionalContext,
        resourceTags: [{ key: 'project', value: c.projectTag }],
        accountIds: [this.account],
        regions: spec.regions,
      },
    });

    // ---------------------------------------------------------------------
    // Outputs. Variant, WorkloadRegion and SecondaryRegion are read back by the
    // deploy scripts: they keep one variant per account and agent Region, and let
    // a destroy find the workload stacks without any option.
    // ---------------------------------------------------------------------
    new CfnOutput(this, 'AgentProfileArn', {
      value: agentProfile.attrArn,
      description: 'Well-Architected Agent profile of this deployment',
    });
    new CfnOutput(this, 'ExecutionRoleArn', {
      value: agentExecutionRole.roleArn,
      description: 'Execution role assumed by the agent service',
    });
    new CfnOutput(this, 'AccessRoleArn', {
      value: agentAccessRole.roleArn,
      description: 'Read-only access role assumed by the execution role',
    });
    new CfnOutput(this, 'Variant', {
      value: spec.variant,
      description: 'Variant of this deployment (single or multi)',
    });
    new CfnOutput(this, 'WorkloadRegion', {
      value: spec.workloadRegion,
      description: 'Region of the workload (primary Region for multi)',
    });
    if (spec.secondaryRegion) {
      new CfnOutput(this, 'SecondaryRegion', {
        value: spec.secondaryRegion,
        description: 'Secondary Region of the multi workload',
      });
    }
  }
}
