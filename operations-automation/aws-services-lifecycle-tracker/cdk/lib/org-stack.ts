import * as cdk from 'aws-cdk-lib';
import * as cloudformation from 'aws-cdk-lib/aws-cloudformation';
import { Construct } from 'constructs';
import { SpokeStack } from './spoke-stack';
import { SPOKE_ROLE_NAME } from './scan-permissions';

export interface OrgStackProps extends cdk.StackProps {
  /** Account running the tracker (the one assuming the spoke roles). */
  hubAccountId: string;
  /** Organization root id (r-xxxx) and/or OU ids (ou-xxxx-yyyyyyyy) to roll the spoke role to. */
  targetOuIds: string[];
  /** Optional sts:ExternalId; must match the hub Pipeline stack. */
  externalId?: string;
}

/**
 * Organization-wide rollout of the spoke role (issue #144).
 *
 * A service-managed CloudFormation StackSet, deployed FROM the hub, that
 * places SpokeStack's read-only role in every account under the given root /
 * OUs and keeps doing so for accounts that join later (auto-deployment).
 * IAM is global, so one region is enough. Requires, in the organization:
 * trusted access for StackSets, and the hub being the management account or a
 * StackSets delegated administrator (shared/scripts/check-org-access verifies
 * both). Optional: single-account deployments never instantiate this stack.
 *
 * The StackSet template IS the SpokeStack, synthesized here from the same
 * code, so a manually deployed spoke and a StackSet-deployed one are identical.
 */
export class OrgStack extends cdk.Stack {
  public readonly stackSetName: string;

  constructor(scope: Construct, id: string, props: OrgStackProps) {
    super(scope, id, props);

    if (!props.targetOuIds.length) {
      throw new Error('targetOuIds must contain at least one root (r-...) or OU (ou-...) id');
    }
    for (const ou of props.targetOuIds) {
      if (!/^(r-[a-z0-9]{4,32}|ou-[a-z0-9]{4,32}-[a-z0-9]{8,32})$/.test(ou)) {
        throw new Error(`'${ou}' is not an organization root or OU id`);
      }
    }

    // Synthesize the spoke template from the same construct (no CDK metadata:
    // the template must be plain CloudFormation for any account).
    // BootstraplessSynthesizer: no assets, so no CDK bootstrap (SSM version
    // parameter / staging bucket) may be required in the member accounts.
    const inner = new cdk.App({ analyticsReporting: false });
    const spoke = new SpokeStack(inner, 'Spoke', {
      hubAccountId: props.hubAccountId,
      externalId: props.externalId,
      synthesizer: new cdk.BootstraplessSynthesizer(),
    });
    const template = inner.synth().getStackByName(spoke.stackName).template;

    this.stackSetName = 'aws-services-lifecycle-tracker-spoke';
    const stackSet = new cloudformation.CfnStackSet(this, 'SpokeStackSet', {
      stackSetName: this.stackSetName,
      description: `Read-only role ${SPOKE_ROLE_NAME} assumed by the lifecycle tracker hub ${props.hubAccountId}`,
      permissionModel: 'SERVICE_MANAGED',
      capabilities: ['CAPABILITY_NAMED_IAM'],
      autoDeployment: { enabled: true, retainStacksOnAccountRemoval: false },
      // Deploy from a delegated administrator as well as from the management account
      callAs: 'SELF',
      templateBody: JSON.stringify(template),
      operationPreferences: {
        failureTolerancePercentage: 100, // one account refusing must not stop the others
        maxConcurrentPercentage: 100,
        regionConcurrencyType: 'PARALLEL',
      },
      stackInstancesGroup: [{
        deploymentTargets: {
          organizationalUnitIds: props.targetOuIds,
          // The hub scans itself with its own credentials: no spoke role there.
          // (For a management-account hub this is redundant: service-managed
          // StackSets never target the management account.)
          accountFilterType: 'DIFFERENCE',
          accounts: [props.hubAccountId],
        },
        regions: [this.region],
      }],
    });

    new cdk.CfnOutput(this, 'StackSetName', { value: stackSet.stackSetName ?? this.stackSetName });
    new cdk.CfnOutput(this, 'TargetOuIds', { value: props.targetOuIds.join(',') });
    new cdk.CfnOutput(this, 'SpokeRoleName', { value: SPOKE_ROLE_NAME });
  }
}
