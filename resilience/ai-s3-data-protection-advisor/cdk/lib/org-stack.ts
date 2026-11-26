import * as cdk from 'aws-cdk-lib';
import * as cloudformation from 'aws-cdk-lib/aws-cloudformation';
import { Construct } from 'constructs';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { SpokeStack } from './spoke-stack';
import { SPOKE_ROLE_NAME } from './scan-permissions';

export interface OrgStackProps extends cdk.StackProps {
  hubAccountId: string;
  hubRegion: string;
  /** Organization root (r-...) and/or OU (ou-...) ids to roll the spoke role out to. */
  targetOuIds: string[];
  /** SELF from the management account, DELEGATED_ADMIN from a StackSets delegated administrator. */
  callAs: 'SELF' | 'DELEGATED_ADMIN';
}

/**
 * Service-managed, auto-deploying StackSet that places the spoke role in every account under the targets, including
 * accounts that join later. Needs trusted access for StackSets and a hub (or a management account acting for it)
 * allowed to run service-managed StackSets: shared/scripts/check-org-access checks both. No tracking description.
 */
export class OrgStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: OrgStackProps) {
    super(scope, id, props);

    if (!props.targetOuIds.length) {
      throw new Error('orgTargets must list at least one root (r-...) or OU (ou-...) id');
    }
    for (const t of props.targetOuIds) {
      if (!/^(r-[a-z0-9]{4,32}|ou-[a-z0-9]{4,32}-[a-z0-9]{8,32})$/.test(t)) {
        throw new Error(`'${t}' is not an organization root or OU id`);
      }
    }

    // The StackSet template is the Spoke stack itself, synthesized bootstrapless: member accounts need no CDK
    // bootstrap, and a hand-deployed spoke is identical to a rolled-out one.
    // Own output folder: the CLI's (CDK_OUTDIR) belongs to the outer app and its manifest.
    const inner = new cdk.App({
      analyticsReporting: false,
      outdir: fs.mkdtempSync(path.join(os.tmpdir(), 's3dp-spoke-')),
    });
    const spoke = new SpokeStack(inner, 'Spoke', {
      hubAccountId: props.hubAccountId,
      hubRegion: props.hubRegion,
      synthesizer: new cdk.BootstraplessSynthesizer(),
    });
    const template = inner.synth().getStackByName(spoke.stackName).template;

    new cloudformation.CfnStackSet(this, 'SpokeStackSet', {
      stackSetName: 'ai-s3-data-protection-advisor-spoke',
      description: `Read-only role ${SPOKE_ROLE_NAME} assumed by the S3 Data Protection Advisor hub ${props.hubAccountId}`,
      permissionModel: 'SERVICE_MANAGED',
      capabilities: ['CAPABILITY_NAMED_IAM'],
      autoDeployment: { enabled: true, retainStacksOnAccountRemoval: false },
      // A service-managed StackSet created from a delegated administrator must say so (DELEGATED_ADMIN).
      callAs: props.callAs,
      templateBody: JSON.stringify(template),
      operationPreferences: {
        failureTolerancePercentage: 100, // one account refusing must not stop the others
        maxConcurrentPercentage: 100,
        regionConcurrencyType: 'PARALLEL',
      },
      stackInstancesGroup: [{
        deploymentTargets: {
          organizationalUnitIds: props.targetOuIds,
          // The hub scans itself with its own role. (Service-managed StackSets never target the management
          // account anyway, so this matters only for a delegated-administrator hub.)
          accountFilterType: 'DIFFERENCE',
          accounts: [props.hubAccountId],
        },
        regions: [this.region], // IAM is global: one region is enough
      }],
    });

    new cdk.CfnOutput(this, 'OrgTargets', { value: props.targetOuIds.join(','), description: 'Roots and OUs targeted' });
    new cdk.CfnOutput(this, 'HubAccountId', { value: props.hubAccountId });
    new cdk.CfnOutput(this, 'CallAs', { value: props.callAs, description: 'StackSet caller role' });
  }
}
