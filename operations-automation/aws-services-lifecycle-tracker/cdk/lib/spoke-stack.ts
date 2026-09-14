import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { HEALTH_READ_ACTIONS, HUB_PIPELINE_ROLE_NAME, SCANNER_READ_ACTIONS, SPOKE_ROLE_NAME } from './scan-permissions';

export interface SpokeStackProps extends cdk.StackProps {
  /** Account that runs the tracker and will assume the spoke role. */
  hubAccountId: string;
  /** Optional sts:ExternalId the hub must present (confused-deputy guard). */
  externalId?: string;
}

/**
 * Spoke side of the multi-account scan (issue #144).
 *
 * Deployed in a MEMBER account, this stack contains exactly one resource: a
 * read-only IAM role the hub's pipeline assumes to run the scanners there.
 * No data, no compute, no Lambda lives in the spoke. The role trusts only the
 * hub account, restricted to the hub pipeline role, optionally with an
 * ExternalId. Its permissions are the scanners' List/Describe calls plus the
 * two AWS Health reads, shared with the hub role through scan-permissions.ts.
 *
 * Deploy it by hand into one account for a test, or let the StackSet in
 * OrgStack roll it out to every account of an organization / OU.
 */
export class SpokeStack extends cdk.Stack {
  public readonly scanRole: iam.Role;

  constructor(scope: Construct, id: string, props: SpokeStackProps) {
    super(scope, id, props);

    if (!/^\d{12}$/.test(props.hubAccountId)) {
      throw new Error(`hubAccountId must be a 12-digit AWS account id, got '${props.hubAccountId}'`);
    }

    const conditions: Record<string, Record<string, string>> = {
      // Trusting the account root keeps the trust policy valid even before the
      // hub role exists; the condition pins it to the pipeline role anyway.
      ArnEquals: { 'aws:PrincipalArn': `arn:${this.partition}:iam::${props.hubAccountId}:role/${HUB_PIPELINE_ROLE_NAME}` },
    };
    if (props.externalId) {
      conditions.StringEquals = { 'sts:ExternalId': props.externalId };
    }

    this.scanRole = new iam.Role(this, 'ScanRole', {
      roleName: SPOKE_ROLE_NAME, // fixed: the hub derives the ARN from the account id
      description: 'Read-only role assumed by the AWS Services Lifecycle Tracker hub to scan this account',
      assumedBy: new iam.PrincipalWithConditions(new iam.AccountPrincipal(props.hubAccountId), conditions),
      maxSessionDuration: cdk.Duration.hours(1),
    });

    this.scanRole.addToPolicy(new iam.PolicyStatement({
      sid: 'LifecycleTrackerScannerReads',
      actions: [...SCANNER_READ_ACTIONS, ...HEALTH_READ_ACTIONS],
      resources: ['*'], // List/Describe calls: read-only, no resource scoping available
    }));

    new cdk.CfnOutput(this, 'ScanRoleArn', {
      value: this.scanRole.roleArn,
      description: 'Role the hub assumes to scan this account',
    });
    new cdk.CfnOutput(this, 'HubAccountId', { value: props.hubAccountId });
  }
}
